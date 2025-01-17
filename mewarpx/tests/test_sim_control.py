import os

import numpy as np

from mewarpx import sim_control
from mewarpx.diags_store.diag_base import WarpXDiagnostic
from mewarpx.mwxrun import mwxrun
from mewarpx.setups_store import diode_setup
from mewarpx.utils_store import testing_util


def test_write_results():
    test_name = "write_results_test"
    testing_util.initialize_testingdir(test_name)

    # Initialize each run with consistent, randomly-chosen, rseed. Use a random
    # seed instead for initial dataframe generation.
    # np.random.seed()
    np.random.seed(83197410)

    STEPS = 1
    D_CA = 0.067 # m
    FREQ = 13.56e6 # MHz
    VOLTAGE = 450.0
    NX = 8
    NZ = 128
    DT = 1.0 / (400 * FREQ)

    run = diode_setup.DiodeRun_V1(
        GEOM_STR='XZ',
        V_ANODE_CATHODE=VOLTAGE,
        V_ANODE_EXPRESSION="%.1f*sin(2*pi*%.5e*t)" % (VOLTAGE, FREQ),
        D_CA=D_CA,
        INERT_GAS_TYPE='He',
        N_INERT=9.64e20,  # m^-3
        T_INERT=300.0,  # K
        PLASMA_DENSITY=2.56e14,  # m^-3
        T_ELEC=30000.0,  # K
        SEED_NPPC=10,
        NX=NX,
        NZ=NZ,
        DT=DT,
        TOTAL_TIMESTEPS=STEPS,
        DIAG_STEPS=STEPS
    )

    run.setup_run(
        init_conductors=False,
        init_scraper=False,
        init_injectors=False,
        init_neutral_plasma=True,
        init_simcontrol=True,
        init_warpx=True,
        init_simulation=True
    )

    def results_contents():
        return f"The dimensions of this run were: {NX} x {NZ}"

    run.control.set_write_func(results_contents)
    run.control.run()

    results_path = os.path.join(WarpXDiagnostic.DIAG_DIR, "results.txt")

    assert os.path.isfile(results_path)

    with open(results_path, 'r') as results_file:
        assert results_file.readline().strip() == f"The dimensions of this run were: {NX} x {NZ}"
