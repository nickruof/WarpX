"""
This facilitates a central place to grab lots of simulation information for our
use.

SETUP order:
    - Import other mewarpx modules
    - Use ``from mewarpx.mwxrun import mwxrun`` to import the class holding
      all the simulation information, defined here.
    - Set up PICMI things, referring to mwxrun's ``picmi.Simulation`` object,
      ``mwxrun.simulation``. Add PICMI species to the PICMI Simulation.

    - Call this class's init_run::

        mwxrun.mwxrun.init_run()

    - Initialize any other mewarpx objects
"""
import atexit
import ctypes
import datetime
import logging
import os.path
import shutil
import sys

import numpy as np
from pywarpx import callbacks, fields, picmi

import mewarpx
from mewarpx.utils_store import init_restart_util
from mewarpx.utils_store import mwxconstants as constants
from mewarpx.utils_store import parallel_util, profileparser

logger = logging.getLogger(__name__)


class MEWarpXRun(object):

    """Hold base information and wrappers for the simulation. This should be a
    singleton object.

    This also does the initialization steps, using init_run to enforce an
    initialization order.
    """

    def __init__(self):
        self.initialized = False
        self.simulation = picmi.Simulation(verbose=0)
        # make a shorthand for simulation.extension since we use it a lot
        self.sim_ext = self.simulation.extension

    def init_grid(self, lower_bound, upper_bound, number_of_cells, use_rz=False,
                  **kwargs):
        """Function to set up the simulation grid.

        Arguments:
            lower_bound (list): Minimum coordinates for all direction; length
                of list has to equal number of dimensions.
            upper_bound (list): Maximum coordinates for all direction; length
                of list has to equal number of dimensions.
            number_of_cells (list): Number of grid cells in each direction.
            use_rz (bool): If True, cylindrical coordinates will be used.
            kwargs (dict): Dictionary containing extra arguments. These can
                include:
                - ``bc_fields_x/y/z/r_min/max``: field boundary condition
                   settings (can be 'periodic', 'dirichlet' or 'none').
                - ``bc_particles_x/y/z/r_min/max``: particle boundary
                  condition settings (can be 'periodic', 'absorbing' or
                  'reflecting').
                - ``min_tiles``: the minimum number of tiles. See function
                  ``_set_max_grid_size()`` below for details.
        """
        self.dim = len(lower_bound)

        # sanity check inputs
        assert len(upper_bound) == self.dim
        assert len(number_of_cells) == self.dim
        if use_rz and self.dim != 2:
            raise RuntimeError(
                f"Cannot use cylindrical coordinates in {self.dim} dimensions."
            )

        self._set_geom_str(use_rz)

        boundary_conditions = self._get_default_boundary_conditions()

        # update boundary conditions if provided
        for key in [key for key in kwargs.keys() if key.startswith('bc')]:
            kind, dim, side = key.split('_')[-3:]
            boundary_conditions[kind][side][self.coord_map[dim]] = kwargs[key]

        if self.geom_str == 'RZ':
            grid = picmi.CylindricalGrid
        elif self.geom_str == 'Z':
            grid = picmi.Cartesian1DGrid
        elif self.geom_str == 'XZ':
            grid = picmi.Cartesian2DGrid
        elif self.geom_str == 'XYZ':
            grid = picmi.Cartesian3DGrid

        self.grid = grid(
            number_of_cells=number_of_cells,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
            lower_boundary_conditions=boundary_conditions['fields']['min'],
            upper_boundary_conditions=boundary_conditions['fields']['max'],
            lower_boundary_conditions_particles=boundary_conditions['particles']['min'],
            upper_boundary_conditions_particles=boundary_conditions['particles']['max'],
        )

        self._set_grid_params()
        self._set_max_grid_size(kwargs.get('min_tiles', None))
        self._print_grid_params()

        # there are a number of initialization tasks that have to happen
        # immediately after init so this function is installed as the
        # first afterinit callback
        if len(callbacks._afterinit) != 0:
            raise RuntimeError(
                "mwxrun._after_init must be the first afterinit callback to "
                "execute, but one was already installed. Please ensure "
                "init_grid is called before other items in the run script."
            )
        callbacks.installafterinit(self._after_init)

        # install a callback to clear the particle boundary buffer before
        # every step, all assemblies for which scraping is enabled will
        # move particles from the particle boundary buffer to their own
        # scraped_particle_array before the E-field solve
        callbacks.installbeforestep(
            self.sim_ext.libwarpx_so.warpx_clearParticleBoundaryBuffer)

    def _set_geom_str(self, use_rz):
        """Function to set the geometry string and coordinate map
        appropriately."""
        self.coord_map = {}
        if self.dim == 1:
            self.geom_str = 'Z'
            self.coord_map['z'] = 0
        elif self.dim == 2:
            if use_rz:
                self.geom_str = 'RZ'
                self.coord_map['r'] = 0
                self.coord_map['z'] = 1
            else:
                self.geom_str = 'XZ'
                self.coord_map['x'] = 0
                self.coord_map['z'] = 1
        elif self.dim == 3:
            self.geom_str = 'XYZ'
            self.coord_map['x'] = 0
            self.coord_map['y'] = 1
            self.coord_map['z'] = 2
        else:
            raise ValueError(
                f"Invalid dimension: {self.dim}"
            )

    def _get_default_boundary_conditions(self):
        """Function to return a dictionary with the default boundary condition
        values for both fields and particles."""
        boundary_conditions = {'fields': {}, 'particles': {}}

        # Set default boundary conditions to be periodic in x and y except for
        # RZ
        if self.geom_str != 'RZ':
            boundary_conditions['fields']['min'] = ['periodic'] * (self.dim - 1)
            boundary_conditions['fields']['max'] = ['periodic'] * (self.dim - 1)
            boundary_conditions['particles']['min'] = (
                ['periodic'] * (self.dim - 1)
            )
            boundary_conditions['particles']['max'] = (
                ['periodic'] * (self.dim - 1)
            )
        else:
            boundary_conditions['fields']['min'] = ['none'] * (self.dim - 1)
            boundary_conditions['fields']['max'] = ['none'] * (self.dim - 1)
            boundary_conditions['particles']['min'] = (
                ['absorbing'] * (self.dim - 1)
            )
            boundary_conditions['particles']['max'] = (
                ['absorbing'] * (self.dim - 1)
            )

        # set default Z boundary conditions
        boundary_conditions['fields']['min'].append('dirichlet')
        boundary_conditions['fields']['max'].append('dirichlet')
        boundary_conditions['particles']['min'].append('absorbing')
        boundary_conditions['particles']['max'].append('absorbing')

        return boundary_conditions

    def _set_grid_params(self):
        """Set xmin, xmax, ymin, ymax, zmin, zmax, rmin, rmax.
        Also set nx, ny, nz and dx, dy, dz.

        I'm not currently sure the best way to store and access these, so this
        is subject to change, but gives a basic interface for now.

        Note PICMI uses x/y for 2D, while WarpX uses x/z for 2D (yay!). We
        stick with WarpX, because emission in +z makes more sense for
        compatibility with RZ.
        """
        # Note similar information is stored by pywarpx.geometry.prob_lo,
        # .prob_hi, and .coord_sys, and by pywarpx.amr.n_cell. Those are
        # set by this solver grid in any case, and the present format matches
        # our existing Python framework better.

        if isinstance(self.grid, picmi.Cartesian1DGrid):
            self.xmin = 0.0
            self.xmax = 1.0
            self.ymin = 0.0
            self.ymax = 1.0
            self.zmin = self.grid.xmin
            self.zmax = self.grid.xmax
            self.rmin = None
            self.rmax = None

            self.nx = 0
            self.ny = 0
            self.nz = self.grid.nx
            self.nr = None

            self.dx = None
            self.dy = None
            self.dz = (self.zmax - self.zmin) / self.nz
            self.dr = None

        elif isinstance(self.grid, picmi.Cartesian2DGrid):
            self.xmin = self.grid.xmin
            self.xmax = self.grid.xmax
            self.ymin = 0.0
            self.ymax = 1.0
            self.zmin = self.grid.ymin
            self.zmax = self.grid.ymax
            self.rmin = None
            self.rmax = None

            self.nx = self.grid.nx
            self.ny = 0
            self.nz = self.grid.ny
            self.nr = None

            self.dx = (self.xmax - self.xmin) / self.nx
            self.dy = None
            self.dz = (self.zmax - self.zmin) / self.nz
            self.dr = None

        elif isinstance(self.grid, picmi.Cartesian3DGrid):
            self.xmin = self.grid.xmin
            self.xmax = self.grid.xmax
            self.ymin = self.grid.ymin
            self.ymax = self.grid.ymax
            self.zmin = self.grid.zmin
            self.zmax = self.grid.zmax
            self.rmin = None
            self.rmax = None

            self.nx = self.grid.nx
            self.ny = self.grid.ny
            self.nz = self.grid.ny
            self.nr = None

            self.dx = (self.xmax - self.xmin) / self.nx
            self.dy = (self.ymax - self.ymin) / self.ny
            self.dz = (self.zmax - self.zmin) / self.nz
            self.dr = None

        elif isinstance(self.grid, picmi.CylindricalGrid):
            self.xmin = self.ymin = self.rmin = self.grid.rmin
            self.xmax = self.ymax = self.rmax = self.grid.rmax
            self.zmin = self.grid.zmin
            self.zmax = self.grid.zmax

            self.nx = self.ny = self.nr = self.grid.nr
            self.nz = self.grid.nz

            self.dx = self.dy = self.dr = (self.rmax - self.rmin) / self.nr
            self.dz = (self.zmax - self.zmin) / self.nz

        else:
            raise ValueError("Unrecognized type of pywarpx.picmi Grid.")

    def _set_max_grid_size(self, min_tiles):
        """Function to set the max_grid_size input parameter appropriately so
        that the simulation will have at least ``min_tiles`` number of tiles.
        """
        if min_tiles is None:
            return

        # appropriately calculate the minimum number of tiles parameter given
        # the simulation dimension
        if self.dim == 1:
            self.grid.max_grid_size = self.nz // min_tiles

        elif self.dim == 2:
            max_nx_nz = max(self.nx, self.nz)
            if min_tiles <= max(self.nx/self.nz, self.nz/self.nx):
                # All the tiles will be sliced in one dimension only
                self.grid.max_grid_size = max_nx_nz // min_tiles
            else:
                # max_grid_size is the smallest factor of 2 that will give at
                # least min_tiles total number of grids
                max_length_per_tile = np.sqrt(self.nx*self.nz/min_tiles)
                self.grid.max_grid_size = (
                    max_nx_nz
                    // int(2. * np.ceil(max_nx_nz / max_length_per_tile / 2.))
                )

        else:
            raise NotImplementedError("3d simulations not implemented yet.")

    def _print_grid_params(self):
        """Function to print out the grid parameters."""
        if self.geom_str == 'RZ':
            logger.info(
                f"Creating RZ grid with nr={self.nr}, nz={self.nz} "
                f"and r, z limits of [[{self.rmin:.4g}, {self.rmax:.4g}], "
                f"[{self.zmin:.4g}, {self.zmax:.4g}]]"
            )
        else:
            logger.info(
                f"Creating {self.geom_str} grid with nx={self.nx}, "
                f"ny={self.ny}, nz={self.nz} and x, y, z limits of "
                f"[[{self.xmin:.4g}, {self.xmax:.4g}], [{self.ymin:.4g}, "
                f"{self.ymax:.4g}], [{self.zmin:.4g}, {self.zmax:.4g}]]"
            )

    def init_timestep(self, DT=None, CFL_factor=None, V_grid=5):
        """Calculate timestep size based on grid data and CFL parameter

         Arguments:
            DT (float): The dt of each step of the simulation, if not given it
                will be calculated.
            CFL_factor (float): Multiplier to determine the actual timestep
                given the CFL ratio. eg. ``dt = CFL_factor * CFL_ratio``.
            V_grid (float): The vaccum bias of highest-voltage grid relative to
                the cathode V. If not defined a value of 5 V is used, which
                is a safe value for finding vmax in the case that the
                electron velocities are only set by their temperature.
        """
        if self.grid is None:
            raise ValueError("init_grid must be called before init_timestep.")

        if DT is not None:
            self.dt = DT
            self.simulation.time_step_size = self.dt
            return self.dt

        if CFL_factor is None:
            raise ValueError(
                "Either CFL-factor or DT should be passed to init_timestep."
            )
        V_grid = abs(V_grid)
        if V_grid < 1:
            raise ValueError(
                "V_grid must be greater than or equal to 1 V to calculate "
                "timestep using CFL_factor."
            )

        vmax = np.sqrt(2*constants.e/constants.m_e * V_grid)

        if self.geom_str == 'XZ' or self.geom_str == 'RZ':
            dt_local = min(self.dx, self.dz) / vmax * CFL_factor
        if self.geom_str == 'XYZ':
            dt_local = min(self.dx, self.dy, self.dz) / vmax * CFL_factor

        self.dt = parallel_util.mpiallreduce(data=dt_local, opstring="MIN")
        self.simulation.time_step_size = self.dt

        return self.dt

    def init_run(self, restart=None, checkpoint_dir="diags",
                 checkpoint_prefix=init_restart_util.DEFAULT_CHECKPOINT_NAME,
                 additional_steps=None):
        if self.initialized:
            raise RuntimeError(
                "Attempted to initialize the mwxrun class multiple times."
            )

        self.restart = restart
        try:
            if self.restart != False:
                force_restart = bool(self.restart)
                self.restart, self.checkpoint_dir, self.checkpoint = (
                    init_restart_util.run_restart(
                        checkpoint_dir, checkpoint_prefix,
                        force_restart, additional_steps
                    )
                )

            self.simulation.initialize_inputs()
            self.simulation.initialize_warpx()

        except RuntimeError:
            if self.restart:
                logger.error(
                    "init_restart_util returned success for restarting from "
                    f"a checkpoint starting with {checkpoint_prefix} "
                    "but there was an error initializing WarpX!"
                )
            else:
                logger.error("There was an error initializing the run!")
            raise

        else:
            logger.info(
                "Successfully initialized WarpX at "
                f"{str(datetime.datetime.now(datetime.timezone.utc))} UTC "
                f"({str(datetime.datetime.now())} local). mewarpx version = "
                f"{mewarpx.__version__} and physics version = "
                f"{mewarpx.__physics_version__}."
            )

        # Ensure all initialization info printed before run starts
        sys.stdout.flush()

    def _after_init(self):
        """This function will be called after WarpX has been initialized but
        before any other `afterinit` callbacks."""
        self.me = self.sim_ext.getMyProc()
        self.n_procs = self.sim_ext.getNProcs()

        # A level is needed for many things like level number. For now I'm
        # statically setting the default level here. I'm not sure of
        # pitfalls or how to handle it more generally yet.
        self.lev = self.sim_ext.libwarpx_so.warpx_finestLevel()

        self.rho_wrappers = [
            fields.RhoFPWrapper(self.lev, False),
            fields.RhoFPWrapper(self.lev, True)
        ]
        self.phi_wrappers = [
            fields.PhiFPWrapper(self.lev, False),
            fields.PhiFPWrapper(self.lev, True)
        ]

        # at this point we are committed to either restarting or starting
        # fresh; if this is a fresh start we can delete diags if present
        if not self.restart and os.path.exists("diags") and self.me == 0:
            shutil.rmtree("diags")

        self.initialized = True

    def get_domain_area(self):
        """Return float of simulation domain area in X & Y directions or R
        depending on geometry. Used to get the surface area over which current
        is emitted or absorbed."""

        if mwxrun.geom_str == "Z":
            return 1.0
        elif mwxrun.geom_str == "XZ":
            return (mwxrun.xmax - mwxrun.xmin)
        elif mwxrun.geom_str == "XYZ":
            return ((mwxrun.xmax - mwxrun.xmin) * (mwxrun.ymax - mwxrun.ymin))
        elif mwxrun.geom_str == "RZ":
            return np.pi * (mwxrun.rmax**2 - mwxrun.rmin**2)
        else:
            raise AttributeError(f"Unknown geometry: {mwxrun.geom_str}")

    def get_it(self):
        """Return the current integer iteration number."""
        return self.sim_ext.getistep(self.lev)

    def get_dt(self):
        """Return the timestep."""
        return self.dt

    def get_t(self):
        """Return the simulation time."""
        return (self.get_it() - 1.0) * self.get_dt()

    def get_npart(self):
        """Get total number of particles in simulation, across all processors.
        """
        npart = 0
        for spec in self.simulation.species:
            npart += spec.get_particle_count()

        return npart

    def get_npart_species_dict(self):
        """Get total number of particles in simulation per species, across all
        processors.
        """
        npart_dict = {}
        for spec in self.simulation.species:
            if spec.name is None:
                raise ValueError("Unnamed species are not supported.")
            npart_dict[spec.name] = spec.get_particle_count()

        return npart_dict

    def get_gathered_rho_grid(self, species_name=None, include_ghosts=False):
        """Get the full rho on the grid on the root processor.

        Arguments:
            species_name (str or None): If specified the charge density for the
                specific species will be returned (deposited on the grid). If
                None, the current state of rho_fp will be returned.
            include_ghosts (bool): Whether or not to include ghost cells.

        Returns:
            A numpy array with rho on the full domain.
        """

        if species_name is not None:
            # clear rho_fp before depositing
            self.rho_wrappers[1][Ellipsis] = 0.0
            self.sim_ext.depositChargeDensity(
                species_name, self.lev, clear_rho=False)

        return self.rho_wrappers[int(include_ghosts)][Ellipsis]

    def get_gathered_phi_grid(self, include_ghosts=False):
        """Get the full phi on the grid.

        Arguments:
            include_ghosts (bool): Whether or not to include ghost cells.

        Returns:
            A numpy array with phi on the full domain.
        """
        return self.phi_wrappers[int(include_ghosts)][Ellipsis]

    def set_phi_grid(self, phi_data):
        """Sets phi on the grid to input phi data.

        Arguments:
            phi_data (numpy array): Phi values on the grid.
        """
        self.phi_wrappers[1][Ellipsis] = phi_data

    def eval_expression_t(self, expr, t=None):
        """Function to evaluate an expression that depends on time, at the
        current simulation time using the WarpX parser.

        Arguments:
            expr (str or float): Expression to evaluate.
            t (float): Optional value of t at which to evaluate the function,
                if not supplied the current simulation time will be used.

        Returns:
            (float) Value of the expression at the current simulation time.
        """
        if isinstance(expr, str):
            if t is not None:
                return self.sim_ext.libwarpx_so.eval_expression_t(
                    ctypes.c_char_p(expr.encode('utf-8')), t
                )
            else:
                return self.sim_ext.libwarpx_so.eval_expression_t(
                    ctypes.c_char_p(expr.encode('utf-8')), self.get_t()
                )
        else:
            return expr

    def move_particles_between_species(self, src_species_name,
                                       dst_species_name):
        """Function to move particles from one particle container to another.
        Particles will be removed from the source particle container.

        Arguments:
            src_species_name (str): The source species name
            dst_species_name (str): The destination species name
        """
        self.sim_ext.libwarpx_so.warpx_moveParticlesBetweenSpecies(
            ctypes.c_char_p(src_species_name.encode('utf-8')),
            ctypes.c_char_p(dst_species_name.encode('utf-8')),
            self.lev
        )

    def calc_Schottky_weight(self, species_name, pre_fac):
        """Function to calculate weight for field-enhanced Schottky emission.

        Arguments:
            species_name (str): The name of the species for which Schottky
                enhancement will be calculated.
            pre_fac (float): Exponent pre-factor in the Schottky enhancement
                calculation -> sqrt(e / 4*pi*eps0) / (kT)
        """
        self.sim_ext.libwarpx_so.warpx_calcSchottkyWeight(
            ctypes.c_char_p(species_name.encode('utf-8')), pre_fac, self.lev
        )


mwxrun = MEWarpXRun()


@atexit.register
def exit_handler():
    # unregister finalize here and call it directly so that the
    # profileparser will have access to the TinyProfiler data
    atexit.unregister(mwxrun.sim_ext.finalize)
    mwxrun.sim_ext.finalize()

    if os.path.isfile("stdout.out") and mwxrun.me == 0:
        profileparser.main("stdout.out")
