"""Usage: amp1.py NAME 
        (--lif | --adex)
        [-a A] [-f F] [-n N]
        [--n_grid NGRID]

Explore oscillation amplitude's effect on communication and computation.

    Arguments:
        NAME    results name (.hdf5)

    Options:
        -h --help               show this screen
        -a A                    maximum oscillation size (amp) [default: 30e-3]
        -f F                    oscillation frequency (Hz) [default: 50]
        -n N                    number of Y neurons [default: 100]
        --n_grid NGRID          N pts. for sampling [0, A] [default: 20]
"""

# %matplotlib inline
# import matplotlib.pyplot as plt
from __future__ import division

import csv

import numpy as np
from docopt import docopt

from fakespikes import util as fsutil
from voltagebudget.neurons import adex, lif
from voltagebudget.util import k_spikes


def create_simulation(nrn, t_stim, N, ns, ts, f, pad=20e-3, Nz=100, **params):
    time = np.max(ts) + pad

    def simulation(A):
        # Create Y, then Z
        ns_y, ts_y, vs_y = nrn(time,
                               N,
                               ns,
                               ts,
                               f=f,
                               A=A,
                               r_b=0,
                               budget=True,
                               report=None,
                               **params)

        # If Y didn't spike, C=0
        if ns_y.shape[0] == 0:
            print("Null Y.")
            return 0.0, 0.0, None

        w_out = 2.0e-9 / N  # Magic number...
        _, ts_z = lif(time,
                      Nz,
                      ns_y,
                      ts_y,
                      w_in=(w_out, w_out / 2),
                      bias=(10e-6, 10e-6 / 5),
                      r_b=0,
                      f=0,
                      A=0,
                      refractory=t_stim + pad,
                      budget=False,
                      report=None)

        # Est comp
        m = np.logical_or(t_stim <= ts_y, ts_y <= (t_stim + pad))
        sigma_y = np.std(ts_y[m])

        # Est communication
        # import ipdb
        # ipdb.set_trace()
        m = np.logical_or(t_stim <= ts_z, ts_z <= (t_stim + pad))
        C = 0
        if ts_z[m].size > 0:
            C = ts_z[m].size / float(Nz)

        return C, sigma_y, vs_y

    return simulation


if __name__ == "__main__":
    args = docopt(__doc__, version='alpha')
    name = args["NAME"]

    N = int(args["-n"])
    Amax = float(args["-a"])
    n_grid = int(args["--n_grid"])
    f = float(args["-f"])

    # ---------------------------------------------------------------------
    t = 0.3

    k = 100
    t_stim = 0.1

    dt = 1e-4
    w = 1e-4
    a = 10000
    ns, ts = k_spikes(t_stim, k, w, a=a, dt=dt, seed=42)
    times = fsutil.create_times(t, dt)

    # ---------------------------------------------------------------------
    if args["--lif"]:
        nrn = lif
        params = dict(w_in=(0.2e-9, 0.2e-9 / 2), bias=(5e-3, 5e-3 / 5))
    elif args["--adex"]:
        nrn = adex
        params = dict(
            w_in=0.3e-9,
            bias=(5e-10, 5e-10 / 5),
            a=(-1.0e-9, 1.0e-9),
            b=(10e-12, 60.0e-12),
            Ereset=(-48e-3, -55e-3))
    else:
        raise ValueError("opt.py requires neuron type --lif or --adex")

    sim = create_simulation(nrn, t_stim, k, ns, ts, f=f, **params)

    As = np.linspace(0.0, Amax, n_grid)
    results = [sim(A) for A in As]

    Cs = [res[0] for res in results]
    sigma_ys = [res[1] for res in results]
    vs = [res[2] for res in results]

    results = dict(As=As, Cs=Cs, sigma_ys=sigma_ys)

    # TODO extract budgets from vs_y
    # TODO Add budgets to scores

    # Write
    keys = sorted(results.keys())
    with open("{}.csv".format(name), "wb") as f:
        writer = csv.writer(f, delimiter=",")
        writer.writerow(keys)
        writer.writerows(zip(*[results[key] for key in keys]))
