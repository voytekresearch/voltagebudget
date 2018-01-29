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
from voltagebudget.util import locate_firsts
from voltagebudget.util import filter_spikes
from voltagebudget.util import budget_window
from voltagebudget.util import locate_peaks
from voltagebudget.util import mad
from voltagebudget.util import mae

from platypus.algorithms import NSGAII
from platypus.core import Problem
from platypus.types import Real


def pareto(name,
           stim,
           E_0,
           N=10,
           t=0.4,
           d=-5e-3,
           w=2e-3,
           T=0.0625,
           f=8,
           A_max=0.5e-9,
           M=100,
           mode='regular',
           noise=False,
           shadow=False,
           save_only=False,
           verbose=False,
           seed_value=42):
    """Optimize using the voltage budget."""
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
        print(">>> Creating reference.")

    ns_ref, ts_ref, _ = adex(
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
        budget=True,
        save_args="{}_ref_args".format(name),
        time_step=time_step,
        seed_value=seed_value,
        **params)

    if ns_ref.size == 0:
        raise ValueError("The reference model didn't spike.")

    # Find E
    # Find the ref spike closest to E_0
    # and set that as E
    if np.isclose(E_0, 0.0):
        _, E = locate_firsts(ns_ref, ts_ref, combine=True)
        if verbose:
            print(">>> Locking on first spike. E was {}.".format(E))
    else:
        E = nearest_spike(ts_ref, E_0)
        if verbose:
            print(">>> E_0 was {}, using closest at {}.".format(E_0, E))

    # Find the phase begin a osc cycle at E 
    phi_E = float(-E * 2 * np.pi * f)

    # Filter ref spikes into the window of interest
    ns_ref, ts_ref = filter_spikes(ns_ref, ts_ref, (E, E + T))
    write_spikes("{}_ref_spks.csv".format(name), ns_ref, ts_ref)

    if verbose:
        print(">>> {} spikes in the analysis window.".format(ns_ref.size))

    # --------------------------------------------------------------
    if verbose:
        print(">>> Setting up the problem function.")

    def sim(pars):
        A_p = pars[0]
        phi_p = pars[1]

        _, _, voltages_o = adex(
            N,
            t,
            ns,
            ts,
            w_in=w_in,
            bias_in=bias_in,
            f=f,
            A=A_p,
            phi=phi_p,
            sigma=sigma,
            time_step=time_step,
            seed_value=seed_value,
            **params)

        # Extract voltages are same time points
        # as the ref
        budget_o = budget_window(voltages_o, E + d, w, select=None)

        # Reduce the voltages
        V_comp = np.mean(budget_o['V_comp'])
        V_osc = np.mean(budget_o['V_osc'])

        if verbose:
            print(
                "(A {:.12f}, phi {:.3f})  ->  (V_comp {:0.5f}, V_osc {:0.5f})".
                format(A_p, phi_p, V_comp, V_osc))

        return V_osc, V_comp

    # --------------------------------------------------------------
    if verbose:
        print(">>> Building problem.")

    problem = Problem(2, 2)
    problem.types[:] = [Real(0, A_max), Real(0.0, np.pi)]

    problem.function = sim
    algorithm = NSGAII(problem)

    # --------------------------------------------------------------
    if verbose:
        print(">>> Running problem.")

    algorithm.run(M)

    # --------------------------------------------------------------
    if verbose:
        print(">>> Analyzing results.")

    # Extract opt params
    As = [s.variables[0] for s in algorithm.result]
    phis = [s.variables[1] for s in algorithm.result]

    results = {}
    results['A'] = As
    results['phis'] = phis

    # Iterate over opt params, analyzing the result
    variances = []
    errors = []
    V_oscs = []
    V_comps = []
    V_frees = []
    As = []
    phis = []
    phis_w = []
    for m in range(M):
        A_m = results['A'][m]
        phi_m = results['phis'][m]

        # Run 
        if verbose:
            print(">>> Running analysis for neuron {}/{}.".format(m + 1, M))

        ns_m, ts_m, voltage_m = adex(
            N,
            t,
            ns,
            ts,
            w_in=w_in,
            bias_in=bias_in,
            f=f,
            A=A_m,
            phi=phi_E,
            sigma=sigma,
            budget=True,
            seed_value=seed_value,
            time_step=time_step,
            save_args="{}_m_{}_opt_args".format(name, m),
            **params)

        # Analyze spikes
        # Filter spikes in E    
        ns_ref, ts_ref = filter_spikes(ns_ref, ts_ref, (E, E + T))
        ns_m, ts_m = filter_spikes(ns_m, ts_m, (E, E + T))

        write_spikes("{}_m_{}_spks".format(name, m), ns_m, ts_m)

        # Variance
        var = mad(ts_m)

        # Error
        error = mae(ts_m, ts_ref)

        # Extract budget values
        budget_m = budget_window(voltage_m, E + d, w, select=None)
        V_osc = np.abs(np.mean(budget_m['V_osc']))
        V_comp = np.abs(np.mean(budget_m['V_comp']))
        V_free = np.abs(np.mean(budget_m['V_free']))

        # Store all stats for n
        variances.append(var)
        errors.append(np.mean(error))

        V_oscs.append(V_osc)
        V_comps.append(V_comp)
        V_frees.append(V_free)

        As.append(A_m)
        phis_w.append(phi_m)
        phis.append(phi_E)

        if verbose:
            print(
                ">>> (A {:0.12f}, phi {:0.3f})  ->  (N spks, {}, mae {:0.5f}, mad, {:0.5f})".
                format(A_m, phi_m, ns_m.size, error, var))

    # --------------------------------------------------------------
    if verbose:
        print(">>> Saving results.")

    # Build a dict of results,
    results = {}
    results["N"] = list(range(N))
    results["variances"] = variances
    results["errors"] = errors

    results["V_osc"] = V_oscs
    results["V_comp"] = V_comps
    results["V_free"] = V_frees

    results["As"] = As
    results["phis"] = phis
    results["phis_w"] = phis_w

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