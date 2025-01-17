"""Tests for functionality in mwxrun.py"""
import logging

import numpy as np
import pytest
import yt

from mewarpx.mwxrun import mwxrun
from mewarpx.setups_store import diode_setup
from mewarpx.utils_store import testing_util


@pytest.mark.parametrize(
    ("name"),
    [
        'Run_RZ',
        'Run_Z',
        'Run_XZ',
        # 'Run3D'
    ]
)
@pytest.mark.filterwarnings("ignore::ResourceWarning")
def test_capacitive_discharge_multigrid(caplog, name):
    caplog.set_level(logging.INFO)
    # Include a random run number to allow parallel runs to not collide. Using
    # python randint prevents collisions due to numpy rseed below
    testing_util.initialize_testingdir(name)

    # Initialize each run with consistent, randomly-chosen, rseed. Use a random
    # seed instead for initial dataframe generation.
    # np.random.seed()
    np.random.seed(92160881)

    GEOM_STR = name.split('_')[-1]

    # Specific numbers match older run for consistency
    FREQ = 13.56e6  # MHz
    DT = 1.0 / (400 * FREQ)
    DIAG_STEPS = 2
    DIAG_INTERVAL = DIAG_STEPS*DT
    VOLTAGE = 450.0
    D_CA = 0.067  # m
    run = diode_setup.DiodeRun_V1(
        GEOM_STR=GEOM_STR,
        V_ANODE_CATHODE=VOLTAGE,
        V_ANODE_EXPRESSION="%.1f*sin(2*pi*%.5e*t)" % (VOLTAGE, FREQ),
        D_CA=D_CA,
        INERT_GAS_TYPE='He',
        N_INERT=9.64e20,  # m^-3
        T_INERT=300.0,  # K
        PLASMA_DENSITY=2.56e14,  # m^-3
        T_ELEC=30000.0,  # K
        SEED_NPPC=16*32,
        NX=16,
        NZ=128,
        DT=DT,
        TOTAL_TIMESTEPS=10,
        DIAG_STEPS=DIAG_STEPS,
        DIAG_INTERVAL=DIAG_INTERVAL
    )
    # Only the functions we change from defaults are listed here
    run.setup_run(
        init_conductors=False,
        init_injectors=False,
        init_neutral_plasma=True,
        init_mcc=True,
        init_field_diag=True,
        init_simcontrol=True,
        init_warpx=True
    )

    # Run the main WARP loop
    run.control.run()

    #######################################################################
    # Cleanup and final output                                            #
    #######################################################################

    all_log_output = ""
    records = caplog.records

    for record in records:
        all_log_output += record.msg + "\n"

    print(all_log_output)
    # make sure out isn't empty
    outstr = "SimControl: Total steps reached."
    assert outstr in all_log_output
