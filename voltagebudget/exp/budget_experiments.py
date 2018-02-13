#!/usr/bin/env python

import fire
import json
import csv
import os
import numpy as np

import voltagebudget
from voltagebudget.neurons import adex
from voltagebudget.neurons import shadow_adex
from voltagebudget.util import poisson_impulse
from voltagebudget.util import read_results
from voltagebudget.util import read_stim
from voltagebudget.util import read_args
from voltagebudget.util import read_modes
from voltagebudget.util import filter_voltages
from voltagebudget.util import locate_firsts
from voltagebudget.util import locate_peaks
from voltagebudget.util import estimate_communication
from voltagebudget.util import precision

from voltagebudget.exp import forward
from voltagebudget.exp import perturb
from voltagebudget.exp import replay
from voltagebudget.exp import pareto
from voltagebudget.exp import reverse
from voltagebudget.exp import create_stim
from voltagebudget.exp import autotune_membrane
from voltagebudget.exp import autotune_w

from platypus.algorithms import NSGAII
from platypus.core import Problem
from platypus.types import Real

from scipy.optimize import least_squares

if __name__ == "__main__":
    fire.Fire({
        'create_stim': create_stim,
        'forward': forward,
        'perturb': perturb,
        'reverse': reverse,
        'pareto': pareto,
        'replay': replay
    })
