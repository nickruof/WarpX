"""
Test that verifies data is plotted correctly from the
Monte-Carlo Collision script based on case 1 from
Turner et al. (2013) - https://doi.org/10.1063/1.4775084
"""

import glob
import os.path

import numpy as np
import pytest
from pywarpx import picmi

from mewarpx.mwxrun import mwxrun
from mewarpx.setups_store import diode_setup
from mewarpx.utils_store import testing_util

constants = picmi.constants


# yt seems to be causing this error when a dataset is loaded
@pytest.mark.filterwarnings("ignore::ResourceWarning")
@pytest.mark.parametrize("plot_on_diag_steps",
[
    True,
    False
])
def test_field_diag(plot_on_diag_steps):
    # We test either post processing or plotting on diag steps, not both.
    post_processing = not plot_on_diag_steps

    plot_diag_str = "_with_diag_plotting" if plot_on_diag_steps else ""
    post_proc_str = "_with_post_processing" if post_processing else ""
    test_name = "field_diag_test" + plot_diag_str + post_proc_str

    testing_util.initialize_testingdir(test_name)

    # Initialize each run with consistent, randomly-chosen, rseed. Use a random
    # seed instead for initial dataframe generation.
    # np.random.seed()
    np.random.seed(83197410)

    STEPS = 10
    D_CA = 0.067 # m
    FREQ = 13.56e6 # MHz
    VOLTAGE = 450.0
    DT = 1.0 / (400 * FREQ)
    DIAG_STEPS = 2 * (int(post_processing) + 1)
    DIAG_DATA_LIST = ['rho_electrons', 'rho_he_ions', 'phi']
    #DIAG_SPECIES_LIST = ["electrons", "he_ions"]
    DIAG_SPECIES_LIST = None

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
        NX=8,
        NZ=128,
        DT=DT,
        TOTAL_TIMESTEPS=STEPS,
        DIAG_STEPS=DIAG_STEPS,
        FIELD_DIAG_DATA_LIST=DIAG_DATA_LIST,
        FIELD_DIAG_SPECIES_LIST=DIAG_SPECIES_LIST,
        FIELD_DIAG_PLOT=plot_on_diag_steps,
        FIELD_DIAG_INSTALL_WARPX=post_processing,
        FIELD_DIAG_PLOT_AFTER_RUN=post_processing
    )

    run.setup_run(
        init_conductors=False,
        init_scraper=False,
        init_injectors=False,
        init_neutral_plasma=True,
        init_field_diag=True,
        init_warpx=True,
        init_simulation=True
    )
    mwxrun.simulation.step()

    # verify that the plot images were created.
    if plot_on_diag_steps:
        print("Verifying that all data and plot files were created...")
        phi_data_n = len(glob.glob(
            os.path.join(
                run.field_diag.write_dir, "Electrostatic_potential_*.npy"
            )
        ))
        phi_plots_n = len(glob.glob(
            os.path.join(
                run.field_diag.write_dir, "Electrostatic_potential_*.png"
            )
        ))
        assert phi_data_n == 5
        assert phi_plots_n == 5
        for species in [species.name for species in mwxrun.simulation.species]:
            n_data = len(glob.glob(
                os.path.join(
                    run.field_diag.write_dir, f"{species}_particle_density_*.npy"
                )
            ))
            n_plots = len(glob.glob(
                os.path.join(
                    run.field_diag.write_dir, f"{species}_particle_density_*.png"
                )
            ))
            assert n_data == 5
            assert n_plots == 5

        print("All plots exist!")

    # verify that the post processing image was created
    if post_processing:
        print("Verifying that all plots were created...")
        # Start at 1st diagnostic (at step 0 all data arrays are 0 and are
        # therefore skipped)
        for i in range(DIAG_STEPS, STEPS - 1, DIAG_STEPS):
            for param in DIAG_DATA_LIST:
                assert os.path.isfile(
                    os.path.join(
                        run.field_diag.write_dir, param + f"_{i:05d}.png"
                    )
                ), param + "_" + f"{i:06d}.png doesn't exist"

        print("All plots exist!")
