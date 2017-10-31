import inspect
import csv
import numpy as np
from brian2 import *
from copy import deepcopy


def shadow_adex(N, time, ns, ts, **adex_kwargs):
    """Est. the 'shadow voltage' of the AdEx membrane voltage."""
    # In the neuron can't fire, we're in the shadow realm!
    Et = 1000  # 1000 volts is infinity, for neurons.
    _, _, budget = adex(time, N, ns, ts, budget=True, Et=Et, **adex_kwargs)

    return budget['V_m'], budget


# TODO: add sigma
def adex(N,
         time,
         ns,
         ts,
         C=200e-12,
         g_l=10e-9,
         V_l=-70e-3,
         a=0e-9,
         b=10e-12,
         tau_w=30e-3,
         E_rheo=-48e-3,
         delta_t=2e-3,
         w_in=0.8e-9,
         tau_in=5e-3,
         bias=0.5e-9,
         Et=-50.4e-3,
         f=0,
         A=1e-3,
         phi=0,
         sigma=0,
         time_step=1e-4,
         budget=True,
         report='text',
         save_args=None,
         seed=None):
    """A AdEx neuron"""
    np.random.seed(seed)
    defaultclock.dt = time_step * second
    prefs.codegen.target = 'numpy'

    # -----------------------------------------------------------------
    if save_args is not None:
        skip = ['ns', 'ts', 'save_args']
        arg_names = inspect.getargspec(adex)[0]

        args = []
        for arg in arg_names:
            if arg not in skip:
                row = (arg, eval(arg))
                args.append(row)

        with open("{}.csv".format(save_args), "wb") as fi:
            writer = csv.writer(fi, delimiter=",")
            writer.writerows(args)

    # -----------------------------------------------------------------
    # If there's no input, return empty 
    if ns.shape[0] == 0:
        return np.array([]), np.array([]), dict()

    # -----------------------------------------------------------------
    # tau_m:
    g_l *= siemens
    C *= farad
    tau_m = C / g_l

    # Other neuron params
    El = V_l * volt
    Et *= volt
    E_rheo *= volt

    # Comp vars
    w_in *= siemens
    tau_in *= second
    bias *= amp
    sigma *= siemens
    a *= siemens
    b *= amp
    delta_t *= volt
    tau_w *= second
    Ecut = Et + 5 * delta_t  # practical threshold condition

    # osc injection
    f *= Hz
    A *= amp
    phi *= second

    # -----------------------------------------------------------------
    # Define neuron and its connections
    eqs = """
    dv/dt = (g_l * (El - v) + g_l * delta_t * exp((v - Et) / delta_t) + I_in + I_osc + I_noise + bias - w) / C : volt
    dw/dt = (a * (v - El) - w) / tau_w : amp
    I_in = g_in * (v - El) : amp
    I_noise = g_noise * (v - El) : amp
    dg_in/dt = -g_in / tau_in : siemens
    I_osc = A * sin((t + phi) * f * 2 * pi) : amp
    dg_noise/dt = -(g_noise+ (sigma * sqrt(tau_in) * xi)) / tau_in : siemens
    """

    P_e = NeuronGroup(
        N,
        model=eqs,
        threshold='v > Ecut',
        reset="v = E_rheo; w += b",
        method='euler')

    # Init
    P_e.v = El
    P_e.w = a * (P_e.v - El)

    # Set up the 'network'
    P_stim = SpikeGeneratorGroup(np.max(ns) + 1, ns, ts * second)
    C_stim = Synapses(P_stim, P_e, on_pre='g_in += w_in')
    C_stim.connect()

    # -----------------------------------------------------------------
    # Deinfe variable
    spikes_e = SpikeMonitor(P_e)
    traces_e = StateMonitor(P_e, ['v'], record=True)

    # Define basic net
    net = Network(P_e, traces_e)
    net.store('no_stim')

    # If budgets are desired, run the net without
    # any stimululation. (This strictly speaking isn't
    # necessary, but I can't get Brian to express the needed
    # diff eq to get the osc budget term in one pass.)
    if budget:
        net.run(time * second, report=report)
        V_osc = deepcopy(np.asarray(traces_e.v_))

    net.restore('no_stim')
    net.add([P_stim, C_stim, spikes_e])
    net.run(time * second, report=report)

    # Extract spikes
    ns_e = np.asarray(spikes_e.i_)
    ts_e = np.asarray(spikes_e.t_)
    result = [ns_e, ts_e]

    if budget:
        E_leak = float(El)
        E_cut = float(Ecut)
        E_rheo = float(E_rheo)

        V_m = np.asarray(traces_e.v_)

        V_m_thesh = V_m.copy()
        V_m_thesh[V_m_thesh > E_rheo] = E_rheo

        V_comp = (V_m_thesh - V_osc) + np.mean(V_osc) - float(El)
        V_osc = E_rheo - V_osc
        V_free = E_rheo - V_m_thesh

        vs = dict(
            tau_m=float(C / g_l),
            times=np.asarray(traces_e.t_),
            V_m=V_m,
            V_m_thresh=V_m_thesh,
            V_comp=V_comp,
            V_osc=V_osc,
            V_free=V_free,
            E_leak=E_leak,
            E_cut=E_cut,
            E_thresh=float(Et))

        result.append(vs)

    return result
