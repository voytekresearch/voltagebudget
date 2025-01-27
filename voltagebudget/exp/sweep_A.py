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
from voltagebudget.util import write_spikes
from voltagebudget.util import write_voltages
from voltagebudget.util import mad
from voltagebudget.util import mae
from voltagebudget.util import select_n
from voltagebudget.util import score_by_group
from voltagebudget.util import score_by_n
from voltagebudget.util import find_E
from voltagebudget.util import find_phis
from voltagebudget.plc import max_deviant
from voltagebudget.plc import uniform
from voltagebudget.plc import coincidence


def sweep_A(name,
            stim,
            E_0,
            A_0=0.00e-9,
            A_max=0.5e-9,
            Z=0.0,
            n_samples=10,
            t=0.4,
            t_pad=0,
            d=-5e-3,
            w=2e-3,
            T=0.0625,
            f=8,
            N=10,
            n_cycles=2,
            mode='regular',
            sigma=0,
            g_l=None,
            V_l=None,
            w_in_0=None,
            w_in_max=None,
            no_lock=False,
            verbose=False,
            save_only=False,
            save_spikes=False,
            save_traces=False,
            seed_value=42):
    """Optimize using the shadow voltage budget."""

    np.random.seed(seed_value)

    # --------------------------------------------------------------
    # Temporal params
    t = t + t_pad

    time_step = 1e-5

    # --------------------------------------------------------------
    if verbose:
        print(">>> Setting mode.")

    params, w_in, bias_in, _ = read_modes(mode)

    # Overide
    if w_in_0 is not None:
        w_in[0] = w_in_0
    if w_in_max is not None:
        w_in[1] = w_in_max

    if g_l is not None:
        params['g_l'] = g_l
    if V_l is not None:
        params['V_l'] = V_l

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
        f=0.0,
        A=0,
        phi=0,
        sigma=sigma,
        budget=True,
        save_args=None,
        time_step=time_step,
        seed_value=seed_value,
        **params)

    if ns_ref.size == 0:
        raise ValueError("The reference model didn't spike.")

    # --------------------------------------------------------------
    # Find E and phis
    E = find_E(E_0, ns_ref, ts_ref, no_lock=no_lock, verbose=verbose)
    phi_w, phi_E = find_phis(E, f, d, verbose=verbose)

    # Filter ref spikes into the window of interest
    ns_ref, ts_ref = filter_spikes(ns_ref, ts_ref, (E, E + T))
    if verbose:
        print(">>> {} spikes in the analysis window.".format(ns_ref.size))

    if save_spikes:
        write_spikes("{}_ref_spks".format(name), ns_ref, ts_ref)

    # --------------------------------------------------------------
    # Rank the neurons
    budget_ref = budget_window(voltages_ref, E + d, w, select=None)
    rank_index = np.argsort(
        [budget_ref["V_free"][j, :].mean() for j in range(N)])

    # --------------------------------------------------------------
    # Init results
    neurons = []
    ranks = []

    variances_pop = []
    errors_pop = []
    n_spikes_pop = []

    variances = []
    errors = []
    n_spikes = []
    n_spike_refs = []

    V_budgets = []
    V_oscs = []
    V_osc_refs = []
    V_comps = []
    V_comp_refs = []
    V_frees = []

    As = []
    biases = []
    phis = []
    phis_w = []

    # --------------------------------------------------------------
    # Run samples
    samples = np.linspace(A_0, A_max, n_samples)

    for i, A_i in enumerate(samples):
        # -
        # Sat homeostasis factor
        bias_adj = bias_in - (A_i * Z)

        if verbose:
            print(">>> Running A {:0.15f} ({}/{}).".format(
                A_i, i + 1, n_samples))
            print(">>> (bias_in {}) -> (bias_adj {})".format(
                bias_in, bias_adj))

        # -
        # Run
        # Spikes, using phi_E
        ns_i, ts_i, voltage_E = adex(
            N,
            t,
            ns,
            ts,
            w_in=w_in,
            bias_in=bias_adj,
            f=f,
            A=A_i,
            phi=phi_E,
            E=E,
            n_cycles=n_cycles,
            sigma=sigma,
            budget=True,
            seed_value=seed_value,
            time_step=time_step,
            save_args=None,
            **params)

        # Est budget contribution of osc as a
        # instant A_i pulse, at E+d+adjustment,
        tau_m = 20e-3
        E_adj = E + d - (1 * tau_m)
        pulse_params = (A_i, E_adj, E_adj + (1 * tau_m))

        _, _, voltage_w = adex(
            N,
            t,
            ns,
            ts,
            E=E_adj,
            n_cycles=0,
            w_in=w_in,
            bias_in=bias_adj,
            f=0,
            A=0,
            phi=0,
            sigma=0,
            budget=True,
            save_args=None,
            time_step=time_step,
            pulse_params=pulse_params,
            seed_value=seed_value,
            **params)

        # -
        # Analyze!
        # Filter spikes in E
        ns_i, ts_i = filter_spikes(ns_i, ts_i, (E, E + T))

        # Pop var and error
        var_pop = mad(ts_i)
        mean_pop = np.mean(ts_i)

        _, error_pop = score_by_n(N, ns_ref, ts_ref, ns_i, ts_i)

        # Budget, all n
        budget_i = budget_window(voltage_w, E + d, w, select=None)

        # -
        # Calc stats for each nth neuron
        for n in range(N):
            # -
            # Select spikes
            ns_ref_n, ts_ref_n = select_n(n, ns_ref, ts_ref)
            ns_i_n, ts_i_n = select_n(n, ns_i, ts_i)

            # Score
            var = mad(ts_i_n, M=mean_pop)
            error = mae(ts_ref_n, ts_i_n)
            n_spike = ts_i_n.size
            n_spike_ref = ts_ref_n.size

            # Extract budget values
            V_b = np.abs(np.mean(budget_i['V_osc'][n]))

            V_osc = np.abs(np.mean(budget_i['V_osc'][n, :]))
            V_comp = np.abs(np.mean(budget_i['V_comp'][n, :]))
            V_free = np.abs(np.mean(budget_i['V_free'][n, :]))

            V_comp_ref = np.abs(np.mean(budget_ref['V_comp'][n, :]))
            V_osc_ref = np.abs(np.mean(budget_ref['V_osc'][n, :]))

            # -
            # Save 'em all
            # Pop
            variances_pop.append(var_pop)
            errors_pop.append(np.mean(error_pop))
            n_spikes_pop.append(ts_i.size)

            # nth
            neurons.append(n)
            ranks.append(rank_index[n])

            variances.append(var)
            errors.append(error)
            n_spikes.append(n_spike)
            n_spike_refs.append(n_spike_ref)

            V_budgets.append(V_b)
            V_oscs.append(V_osc)
            V_osc_refs.append(V_osc_ref)
            V_comps.append(V_comp)
            V_comp_refs.append(V_comp_ref)
            V_frees.append(V_free)

            # Repeats: tidy data
            As.append(A_i)
            biases.append(bias_adj)
            phis.append(phi_E)
            phis_w.append(phi_w)

        # -
        if verbose:
            print(
                ">>> (A {:0.12f})  ->  (N spks, {}, mae {:0.5f}, mad, {:0.5f})"
                .format(A_i, ns_i.size / float(N), error_pop, var_pop))

        if save_spikes or save_traces:
            print(">>> Writing details for A {} (nA)".format(
                np.round(A_i * 1e9, 3)))
        if save_spikes:
            write_spikes("{}_A{}_spks".format(name, np.round(A_i * 1e9, 3)),
                         ns_i, ts_i)
        if save_traces:
            write_voltages(
                "{}_A{}_w".format(name, np.round(A_i * 1e9, 3)),
                voltage_w,
                select=["V_comp", "V_osc", "V_m"])
            write_voltages(
                "{}_A{}_E".format(name, np.round(A_i * 1e9, 3)),
                voltage_E,
                select=["V_comp", "V_osc", "V_m"])

    # --------------------------------------------------------------
    if verbose:
        print(">>> Saving results.")

    # Build a dict of results,
    results = {}

    results["N"] = neurons
    results["rank"] = ranks

    results["variances_pop"] = variances_pop
    results["errors_pop"] = errors_pop
    results["n_spikes_pop"] = n_spikes_pop

    results["variances"] = variances
    results["errors"] = errors
    results["n_spikes"] = n_spikes
    results["n_spikes_ref"] = n_spike_refs

    results["V_b"] = V_budgets

    results["V_osc"] = V_oscs
    results["V_comp"] = V_comps
    results["V_free"] = V_frees

    results["V_osc_ref"] = V_osc_refs
    results["V_comp_ref"] = V_comp_refs

    results["As"] = As
    results["biases"] = biases
    results["phis_E"] = phis
    results["phis_w"] = phis_w

    # then write it out.
    keys = sorted(results.keys())
    with open("{}.csv".format(name), "w") as fi:
        writer = csv.writer(fi, delimiter=",")
        writer.writerow(keys)
        writer.writerows(zip(*[results[key] for key in keys]))

    # If running in a CL, returns are line noise?
    if not save_only:
        return results
    else:
        return None


def optimal_sweep_A(name,
                    stim,
                    E_0,
                    A_0=0.00e-9,
                    A_max=0.02e-9,
                    Z=0.0,
                    n_samples=10,
                    t=0.4,
                    t_pad=0,
                    d=-5e-3,
                    w=2e-3,
                    T=0.0625,
                    f=8,
                    N=10,
                    n_cycles=2,
                    mode='regular',
                    optimal_method='max_deviant',
                    sigma=0,
                    dt=0.01e-3,
                    w_in_0=None,
                    w_in_max=None,
                    no_lock=False,
                    verbose=False,
                    save_only=False,
                    save_spikes=False,
                    seed_value=42):
    """Optimize using the shadow voltage budget."""
    np.random.seed(seed_value)

    # --------------------------------------------------------------
    # Temporal params
    t = t + t_pad
    time_step = 1e-5

    # --------------------------------------------------------------
    if verbose:
        print(">>> Setting mode.")

    params, w_in, bias_in, _ = read_modes(mode)

    # Overide default w_in?
    if w_in_0 is not None:
        w_in[0] = w_in_0
    if w_in_max is not None:
        w_in[1] = w_in_max

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

    ns_ref, ts_ref = adex(
        N,
        t,
        ns,
        ts,
        w_in=w_in,
        bias_in=bias_in,
        f=0.0,
        A=0,
        phi=0,
        sigma=sigma,
        budget=False,
        save_args=None,
        time_step=time_step,
        seed_value=seed_value,
        **params)

    if ns_ref.size == 0:
        raise ValueError("The reference model didn't spike.")

    # --------------------------------------------------------------
    # Find E and phis
    E = find_E(E_0, ns_ref, ts_ref, no_lock=no_lock, verbose=verbose)
    phi_w, phi_E = find_phis(E, f, d, verbose=verbose)

    # Filter ref spikes into the window of interest
    ns_ref, ts_ref = filter_spikes(ns_ref, ts_ref, (E, E + T))
    var_ref = mad(ts_ref)

    if verbose:
        print(">>> {} spikes in the analysis window.".format(ns_ref.size))

    if save_spikes:
        write_spikes("{}_ref_spks".format(name), ns_ref, ts_ref)

    # --------------------------------------------------------------
    # Rank the neurons

    # --------------------------------------------------------------
    # Init results
    neurons = []
    ranks = []

    variances_pop = []
    variances_ref = []
    variances_opt = []
    errors_pop = []
    errors_opt = []
    n_spikes_pop = []
    phis = []

    # --------------------------------------------------------------
    # Run samples
    samples = np.linspace(A_0, A_max, n_samples)

    for i, A_i in enumerate(samples):
        # -
        # Sat homeostasis factor
        bias_adj = bias_in - (A_i * Z)

        if verbose:
            print(">>> Running A {:0.15f} ({}/{}).".format(
                A_i, i + 1, n_samples))
            print(">>> (bias_in {}) -> (bias_adj {})".format(
                bias_in, bias_adj))

        # -
        # Run
        # Spikes, using phi_E
        ns_i, ts_i = adex(
            N,
            t,
            ns,
            ts,
            w_in=w_in,
            bias_in=bias_adj,
            f=f,
            A=A_i,
            phi=phi_E,
            E=E,
            n_cycles=n_cycles,
            sigma=sigma,
            budget=False,
            seed_value=seed_value,
            time_step=time_step,
            save_args=None,
            **params)

        # -
        # Analyze!
        # Filter spikes in E
        ns_i, ts_i = filter_spikes(ns_i, ts_i, (E, E + T))

        # Pop var and error by oscillation
        var_pop = mad(ts_i)
        _, error_pop = score_by_n(N, ns_ref, ts_ref, ns_i, ts_i)
        n_spikes = ts_i.size

        # Pop var and error by oscillation by optimal method
        if optimal_method == 'max_deviant':
            _, _, var_opt, ts_opt = max_deviant(
                ts_ref,
                var_ref,
                var_pop,
                side='both',
                mode_fm=np.median,
                dt=dt)
        elif optimal_method == 'uniform':
            _, _, var_opt, ts_opt = uniform(ts_ref, var_ref, var_pop)
        elif optimal_method == 'coincidence':
            _, _, var_opt, ts_opt = coincidence(ts_ref, var_ref, var_pop)
        else:
            raise ValueError("Otimmal method not known")

        # Est error for opt
        _, error_opt = score_by_n(N, ns_ref, ts_ref, ns_ref, ts_opt)

        # -
        # Save
        variances_pop.append(var_pop)
        variances_ref.append(var_ref)
        variances_opt.append(var_opt)
        errors_pop.append(error_pop)
        errors_opt.append(error_opt)
        n_spikes_pop.append(n_spikes)
        phis.append(phi_E)

        # -
        if verbose:
            print(
                ">>> (A {:0.12f})  ->  (N spks, {}, mae {:0.5f}, mad, {:0.5f})"
                .format(A_i, ns_i.size / float(N), error_pop, var_pop))

        if save_spikes:
            print(">>> Writing details for A {} (nA)".format(
                np.round(A_i * 1e9, 3)))
        if save_spikes:
            write_spikes("{}_A{}_spks".format(name, np.round(A_i * 1e9, 3)),
                         ns_i, ts_i)
            write_spikes(
                "{}_A{}_spks_opt".format(name, np.round(A_i * 1e9, 3)), ns_i,
                ts_opt)

    # --------------------------------------------------------------
    if verbose:
        print(">>> Saving results.")

    # Build a dict of results,
    results = {}
    results["variances_ref"] = variances_ref
    results["variances_pop"] = variances_pop
    results["variances_opt"] = variances_opt
    results["errors_pop"] = errors_pop
    results["n_spikes_pop"] = n_spikes_pop
    results["errors_opt"] = errors_opt
    results["As"] = samples
    results["phis_E"] = phis

    # then write it out.
    keys = sorted(results.keys())
    with open("{}.csv".format(name), "w") as fi:
        writer = csv.writer(fi, delimiter=",")
        writer.writerow(keys)
        writer.writerows(zip(*[results[key] for key in keys]))

    # If running in a CL, returns are line noise?
    if not save_only:
        return results
    else:
        return None