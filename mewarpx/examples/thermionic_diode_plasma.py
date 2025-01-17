import argparse
import sys

import numpy as np

from mewarpx.mwxrun import mwxrun
from mewarpx.setups_store import diode_setup


class PlanarPlasmaTEC(object):

    #######################################################################
    # Begin global user parameters                                        #
    #######################################################################

    CATHODE_TEMP = 1100 + 273.15 # K
    CATHODE_A = 6e5              # A/m^2/K^2
    CATHODE_PHI = 2.11           # eV
    V_CATHODE = -CATHODE_PHI     # cathode is grounded

    ANODE_TEMP = 200             # K
    ANODE_PHI = 1.4              # eV

    D_CA = 50e-6                 # m

    P_INERT = 2                  # Torr
    T_INERT = 800                # K

    NZ = 128
    NX = 8

    DT = 0.5e-12                 # s

    NPPC = 100

    #######################################################################
    # End global user parameters and user input                           #
    #######################################################################

    def __init__(self, V_ANODE_CATHODE, TOTAL_TIMESTEPS, DIAG_STEPS=None,
                 USE_SCHOTTKY=False):

        self.V_ANODE_CATHODE = V_ANODE_CATHODE
        self.TOTAL_TIMESTEPS = TOTAL_TIMESTEPS
        self.USE_SCHOTTKY = USE_SCHOTTKY

        self.DIAG_STEPS = DIAG_STEPS
        if self.DIAG_STEPS is None:
            self.DIAG_STEPS = self.TOTAL_TIMESTEPS // 5

    def setup_run(self):

        ####################################
        # Diode setup
        ####################################

        self.run = diode_setup.DiodeRun_V1(
            GEOM_STR='XZ',
            CATHODE_TEMP=self.CATHODE_TEMP,
            CATHODE_A=self.CATHODE_A,
            CATHODE_PHI=self.CATHODE_PHI,
            USE_SCHOTTKY=self.USE_SCHOTTKY,
            ANODE_TEMP=self.ANODE_TEMP,
            ANODE_PHI=self.ANODE_PHI,
            V_ANODE_CATHODE=self.V_ANODE_CATHODE,
            D_CA=self.D_CA,
            DT=self.DT,
            P_INERT=self.P_INERT,
            T_INERT=self.T_INERT,
            NX=self.NX,
            NZ=self.NZ,
            DIRECT_SOLVER=True,
            NPPC=self.NPPC,
            TOTAL_TIMESTEPS=self.TOTAL_TIMESTEPS,
            DIAG_STEPS=self.DIAG_STEPS
        )

        # Only the functions we change from defaults are listed here
        self.run.setup_run(
            init_mcc=True,
            init_runinfo=True,
            init_fluxdiag=True,
            init_simcontrol=True,
            init_warpx=True
        )

    def run_sim(self):

        #################################
        # Simulation run
        #################################

        mwxrun.simulation.step(self.TOTAL_TIMESTEPS)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--V', help='bias voltage in Volt', type=float,
                        default=0)
    parser.add_argument('--steps', help='set the number of simulation steps',
                        type=int, default=3000)
    parser.add_argument('--schottky', help='use Schottky enhancement',
                        default=False, action='store_true')
    args, left = parser.parse_known_args()
    sys.argv = sys.argv[:1]+left

    run = PlanarPlasmaTEC(args.V, args.steps, USE_SCHOTTKY=args.schottky)
    run.setup_run()
    run.run_sim()
