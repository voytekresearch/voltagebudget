import os
import json
import csv
import numpy as np

import voltagebudget
from voltagebudget.neurons import adex
from voltagebudget.neurons import shadow_adex
from voltagebudget.util import poisson_impulse
from voltagebudget.util import read_results
from voltagebudget.util import read_stim
from voltagebudget.util import read_args
from voltagebudget.util import read_modes
from voltagebudget.util import nearest_spike
from voltagebudget.util import write_spikes

from voltagebudget.budget import locate_firsts
from voltagebudget.budget import filter_spikes
from voltagebudget.budget import budget_window
from voltagebudget.budget import locate_peaks
from voltagebudget.budget import estimate_communication
from voltagebudget.budget import precision
from voltagebudget.exp.autotune import autotune_V_osc


def forward_shadow(name,
                   stim,
                   E_0,
                   N=10,
                   t=0.4,
                   d=-5e-3,
                   w=2e-3,
                   T=0.0625,
                   f=8,
                   A_0=.05e-9,
                   A_max=0.5e-9,
                   phi_0=np.pi,
                   mode='regular',
                   opt_f=False,
                   noise=False,
                   save_only=False,
                   verbose=False,
                   seed_value=42):
    """Optimize using the shadow voltage budget."""
    np.random.seed(seed_value)

    # --------------------------------------------------------------
    # Temporal params
    time_step = 1e-5
    coincidence_t = 1e-3

    # --------------------------------------------------------------
    if verbose:
        print(">>> Setting mode.")

    params, w_in, bias_in, sigma = read_modes(mode)
    if not noise:
        sigma = 0

    # --------------------------------------------------------------
    if verbose:
        print(">>> Importing stimulus from {}.".format(stim))

    stim_data = read_stim(stim)
    ns = np.asarray(stim_data['ns'])
    ts = np.asarray(stim_data['ts'])

    # --------------------------------------------------------------
    # Define target computation (i.e., no oscillation)
    # (TODO Make sure and explain this breakdown well in th paper)
    if verbose:
        print(">>> Creating reference spikes.")

    ns_ref, ts_ref, voltages_ref = adex(
        N,
        t,
        ns,
        ts,
        w_in=w_in,
        bias_in=bias_in,
        f=0,
        A=0,
        phi=0,
        sigma=sigma,
        seed_value=seed_value,
        budget=True,
        save_args="{}_ref_args".format(name),
        time_step=time_step,
        **params)

    if ns_ref.size == 0:
        raise ValueError("The reference model didn't spike.")

    # Find the ref spike closest to E_0
    # and set that as E
    E = nearest_spike(ts_ref, E_0)
    if verbose:
        print(">>> E_0 was {}, using closest at {}.".format(E_0, E))

    # Filter ref spikes into the window of interest
    ns_ref, ts_ref = filter_spikes(ns_ref, ts_ref, (E, E + T))
    write_spikes("{}_ref_spks.csv".format(name), ns_ref, ts_ref)

    if verbose:
        print(">>> {} spikes in the analysis window.".format(ns_ref.size))

    # -
    if verbose:
        print(">>> Creating shadow reference.")

    shadow_ref = shadow_adex(
        N,
        t,
        ns,
        ts,
        w_in=w_in,
        bias_in=bias_in,
        f=0,
        A=0,
        phi=0,
        sigma=sigma,
        seed_value=seed_value,
        **params)

    # --------------------------------------------------------------
    if verbose:
        print(">>> Begining budget estimates:")
    solutions = autotune_V_osc(
        N,
        t,
        E,
        d,
        ns,
        ts,
        A_0=A_0,
        A_max=A_max,
        phi_0=phi_0,
        f=f,
        verbose=verbose)

    # --------------------------------------------------------------
    if verbose:
        print(">>> Analyzing results.")

    communication_scores = []
    computation_scores = []
    communication_voltages = []
    computation_voltages = []
    for n, sol in enumerate(solutions):
        A_opt, phi_opt = sol.x

        # Run 
        if verbose:
            print(">>> Running analysis for neuron {}/{}.".format(n + 1, N))

        ns_n, ts_n, voltage_n = adex(
            N,
            t,
            ns,
            ts,
            w_in=w_in,
            bias_in=bias_in,
            f=f,
            A=A_opt,
            phi=phi_opt,
            sigma=sigma,
            budget=True,
            seed_value=seed_value,
            time_step=time_step,
            save_args="{}_n_{}_opt_args".format(name, n),
            **params)

        # Analyze spikes
        # Coincidences
        comm = estimate_communication(
            ns_n,
            ts_n, (E, E + T),
            coincidence_t=coincidence_t,
            time_step=time_step)

        # Precision
        ns_ref, ts_ref = filter_spikes(ns_ref, ts_ref, (E, E + T))
        ns_n, ts_n = filter_spikes(ns_n, ts_n, (E, E + T))
        _, prec = precision(ns_n, ts_n, ns_ref, ts_ref, combine=True)

        # Extract budget values
        budget_n = budget_window(voltage_n, E + d, w, select=None)
        V_osc = np.abs(np.mean(budget_n['V_osc'][n, :]))
        V_comp = np.abs(np.mean(budget_n['V_comp'][n, :]))

        # Store all stats for n
        communication_scores.append(comm)
        computation_scores.append(np.mean(prec))

        communication_voltages.append(V_osc)
        computation_voltages.append(V_comp)

    # --------------------------------------------------------------
    if verbose:
        print(">>> Saving results.")

    # Build a dict of results,
    results = {}
    results["communication_scores"] = communication_scores
    results["computation_scores"] = computation_scores
    results["communication_voltages"] = communication_voltages
    results["computation_voltages"] = computation_voltages

    # then write it out.
    keys = sorted(results.keys())
    with open("{}.csv".format(name), "w") as fi:
        writer = csv.writer(fi, delimiter=",")
        writer.writerow(keys)
        writer.writerows(zip(* [results[key] for key in keys]))

    # If running in a CL, returns are line noise?
    if not save_only:
        return results
    else:
        return None