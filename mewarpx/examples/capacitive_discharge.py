"""
Monte-Carlo Collision script based on case 1 from
Turner et al. (2013) - https://doi.org/10.1063/1.4775084
"""

from mewarpx.utils_store import util as mwxutil
mwxutil.init_libwarpx(ndim=2, rz=False)

from mewarpx.mwxrun import mwxrun
from mewarpx.diags_store import diag_base
from mewarpx.diags_store.field_diagnostic import FieldDiagnostic
from mewarpx import mespecies, emission, mcc_wrapper, poisson_pseudo_1d

from pywarpx import picmi, _libwarpx, callbacks

import numpy as np

import sys
import argparse

constants = picmi.constants

##########################
# physics parameters
##########################

D_CA = 0.067 # m

N_INERT = 9.64e20 # m^-3
T_INERT = 300.0 # K

FREQ = 13.56e6 # MHz

VOLTAGE = 450.0

M_ION = 6.67e-27 # kg

PLASMA_DENSITY = 2.56e14 # m^-3
T_ELEC = 30000.0 # K

SEED_NPPC = 16 * 32

##########################
# numerics parameters
##########################

# --- Grid
nx = 8
nz = 128

xmin = 0.0
zmin = 0.0
xmax = D_CA / nz * nx
zmax = D_CA

DT = 1.0 / (400 * FREQ)

# Total simulation time in seconds
TOTAL_TIME = 1280 / FREQ
# Time (in seconds) between diagnostic evaluations
DIAG_INTERVAL = 32 / FREQ

# --- Number of time steps
MAX_STEPS = int(TOTAL_TIME / DT)
DIAG_STEPS = int(DIAG_INTERVAL / DT)

parser = argparse.ArgumentParser()
parser.add_argument('--steps', help='set the number of simulation steps manually', type=int)
args, left = parser.parse_known_args()
sys.argv = sys.argv[:1]+left

print('Setting up simulation with')
print(f'  dt = {DT:3e} s')
if args.steps:
    max_steps = args.steps
    diag_steps = max_steps // 5
else:
    max_steps = MAX_STEPS
    diag_steps = DIAG_STEPS
print(f' Total time = {DT*max_steps:3e} ({max_steps} timesteps)')

##########################
# physics components
##########################

anode_voltage = f"{VOLTAGE}*sin(2*pi*{FREQ:.5e}*t)"

#####################################
# grid, solver and timesteps
#####################################

mwxrun.init_grid(xmin, xmax, zmin, zmax, nx, nz)
mwxrun.grid.potential_zmax = anode_voltage

# solver = picmi.ElectrostaticSolver(
#    grid=mwxrun.grid, method='Multigrid', required_precision=1e-12
# )
solver = poisson_pseudo_1d.PoissonSolverPseudo1D(grid=mwxrun.grid)

mwxrun.simulation.solver = solver
mwxrun.init_timestep(DT=DT)
mwxrun.simulation.max_steps = max_steps

##########################
# declare species
##########################

electrons = mespecies.Species(particle_type='electron', name='electrons')
ions = mespecies.Species(particle_type='He', name='he_ions', charge='q_e')

##########################
# neutral plasma injection
##########################

vol_emitter = emission.UniformDistributionVolumeEmitter(T=T_ELEC)

plasma_injector = emission.PlasmaInjector(
    emitter=vol_emitter, species1=electrons, species2=ions,
    npart=2 * SEED_NPPC * nx * nz,
    T_2=T_INERT, plasma_density=PLASMA_DENSITY
)

##########################
# collision physics
##########################

# MCC collisions
mcc = mcc_wrapper.MCC(
    electrons, ions, T_INERT=T_INERT, N_INERT=N_INERT,
    exclude_collisions=['charge_exchange']
)

##########################
# diagnostics
##########################

field_diag = FieldDiagnostic(
    diag_steps=diag_steps, barrier_slices=[xmax/2.0],
    save_pdf=False, style='roelof', min_dim=2.0
)

##########################
# WarpX and mewarpx initialization
##########################

mwxrun.init_run()

##########################
# Add ME diagnostic
##########################

text_diag = diag_base.TextDiag(diag_steps=diag_steps, preset_string='perfdebug')

rho_array = np.zeros(129)
def _get_rho_ions():
    global rho_array
    rho_data = mwxrun.get_gathered_rho_grid('he_ions', False)
    if mwxrun.me == 0:
        rho_array += (
            np.mean(rho_data[:,:,0], axis=0) / constants.q_e / DIAG_STEPS
        )

##########################
# simulation run
##########################
if args.steps:
    mwxrun.simulation.step(args.steps)
    text_diag.print_performance_summary()
else:
    mwxrun.simulation.step(MAX_STEPS - DIAG_STEPS)
    callbacks.installafterstep(_get_rho_ions)
    mwxrun.simulation.step(DIAG_STEPS)

    ##########################
    # collect diagnostics
    ##########################

    if mwxrun.me == 0:
        np.save('avg_rho_data.npy', rho_array)
