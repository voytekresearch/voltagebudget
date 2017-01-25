"""Usage: opt1.py NAME N 
        (--lif | --adex)
        [-a A]

Minimize A and minimize sigma_Y. 

(The smallest oscillation that minimizes Y spiking std. dev.)

    Arguments:
        NAME    results name (.hdf5)
        N       number of interations

    Options:
        -h --help               show this screen
        -a A                    maximum oscillation size [default: 30e-3]
"""

# %matplotlib inline
# import matplotlib.pyplot as plt
from __future__ import division

import csv

import numpy as np
from docopt import docopt

from fakespikes import util as fsutil
from platypus.algorithms import NSGAII
from platypus.core import Problem
from platypus.types import Real
from voltagebudget.neurons import adex, lif
from voltagebudget.util import k_spikes


def create_problem(nrn, t_stim, N, ns, ts, f, pad=10e-3, Nz=100, **params):
    time = np.max(ts) + pad

    def problem(A):
        A = A[0]

        # Create Y, then Z
        ns_y, ts_y = nrn(time,
                         N,
                         ns,
                         ts,
                         f=f,
                         A=A,
                         r_b=0,
                         budget=False,
                         report=None,
                         **params)

        # If Y didn't spike, C=0
        if ns_y.shape[0] == 0:
            print("Null Y.")
            return A, np.inf

        m = np.logical_or(t_stim <= ts_y, ts_y <= (t_stim + pad))
        y_sigma = np.std(ts_y[m])

        print(A, y_sigma)
        return A, y_sigma

    return problem


if __name__ == "__main__":
    args = docopt(__doc__, version='alpha')
    name = args["NAME"]
    N = int(args["N"])
    Amax = float(args["-a"])

    # ---------------------------------------------------------------------
    t = 0.3

    k = 20
    t_stim = 0.1

    dt = 1e-4
    w = 1e-4
    a = 10000
    ns, ts = k_spikes(t_stim, k, w, a=a, dt=dt, seed=42)
    times = fsutil.create_times(t, dt)

    # ---------------------------------------------------------------------
    f = 50
    if args["--lif"]:
        nrn = lif
        params = dict(w_in=(0.2e-9, 0.2e-9 / 10), bias=(5e-3, 5e-3 / 10))
    elif args["--adex"]:
        nrn = adex
        params = dict(
            w_in=0.3e-9,
            bias=(5e-10, 5e-10 / 20),
            a=(-1.0e-9, 1.0e-9),
            b=(10e-12, 60.0e-12),
            Ereset=(-48e-3, -55e-3))
    else:
        raise ValueError("opt.py requires neuron type --lif or --adex")

    sim = create_problem(nrn, t_stim, k, ns, ts, f=f, **params)

    # ---------------------------------------------------------------------
    problem = Problem(1, 2)
    problem.types[:] = Real(0.0, Amax)

    problem.function = sim
    algorithm = NSGAII(problem)
    algorithm.run(N)

    results = dict(
        As=[s.objectives[0] for s in algorithm.result],
        y_sigmas=[s.objectives[1] for s in algorithm.result])

    keys = sorted(results.keys())
    with open("{}.csv".format(name), "wb") as f:
        writer = csv.writer(f, delimiter=",")
        writer.writerow(keys)
        writer.writerows(zip(*[results[key] for key in keys]))
