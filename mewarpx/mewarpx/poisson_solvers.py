from functools import partial
import logging

import numpy as np
from pywarpx import callbacks
from pywarpx.picmi import Cartesian2DGrid, ElectrostaticSolver, constants
from scipy.sparse import csc_matrix
from scipy.sparse import linalg as sla

from mewarpx.mwxrun import mwxrun

logger = logging.getLogger(__name__)


class PoissonSolverPseudo1D(ElectrostaticSolver):

    def __init__(self, grid, **kwargs):
        """Direct solver for the Poisson equation using superLU. This solver is
        useful for pseudo 1D cases i.e. diode simulations with small x extent.

        Arguments:
            grid (picmi.Cartesian2DGrid): Instance of the grid on which the
            solver will be installed.
        """
        # Sanity check that this solver is appropriate to use
        if not isinstance(grid, Cartesian2DGrid):
            raise RuntimeError('Direct solver can only be used on a 2D grid.')

        super(PoissonSolverPseudo1D, self).__init__(
            grid=grid, method=kwargs.pop('method', 'Multigrid'),
            required_precision=1, **kwargs
        )

    def initialize_inputs(self):
        """Grab geometrical quantities from the grid. The boundary potentials
        are also obtained from the grid using 'warpx_potential_zmin' for the
        left_voltage and 'warpx_potential_zmax' for the right_voltage.
        These can be given as floats or strings that can be parsed by the
        WarpX parser.
        """
        # grab the boundary potentials from the grid object
        left_voltage = self.grid.potential_zmin
        if left_voltage is None:
            left_voltage = 0.0
        self.left_voltage = partial(mwxrun.eval_expression_t, left_voltage)

        right_voltage = self.grid.potential_zmax
        if right_voltage is None:
            right_voltage = 0.0
        self.right_voltage = partial(mwxrun.eval_expression_t, right_voltage)

        # set WarpX boundary potentials to None since we will handle it
        # ourselves in this solver
        self.grid.potential_xmin = None
        self.grid.potential_xmax = None
        self.grid.potential_ymin = None
        self.grid.potential_ymax = None
        self.grid.potential_zmin = None
        self.grid.potential_zmax = None

        super(PoissonSolverPseudo1D, self).initialize_inputs()

        self.nx = self.grid.nx
        self.nz = self.grid.ny
        self.dx = (self.grid.xmax - self.grid.xmin) / self.nx
        self.dz = (self.grid.ymax - self.grid.ymin) / self.nz

        if not np.isclose(self.dx, self.dz):
            raise RuntimeError('Direct solver requires dx = dz.')

        self.nxguardphi = 1
        self.nzguardphi = 1

        self.phi = np.zeros(
            (self.nx + 1 + 2*self.nxguardphi,
            self.nz + 1 + 2*self.nzguardphi)
        )

        self.decompose_matrix()

        logger.info("Using direct solver.")
        callbacks.installpoissonsolver(self._run_solve)

    def decompose_matrix(self):
        """Function to build the superLU object used to solve the linear
        system."""
        self.nzsolve = self.nz + 1
        self.nxsolve = self.nx + 3

        # Set up the computation matrix in order to solve A*phi = rho
        A = np.zeros(
            (self.nzsolve*self.nxsolve, self.nzsolve*self.nxsolve)
        )
        kk = 0
        for ii in range(self.nxsolve):
            for jj in range(self.nzsolve):
                temp = np.zeros((self.nxsolve, self.nzsolve))

                if jj == 0 or jj == self.nzsolve - 1:
                    temp[ii, jj] = 1.
                elif jj == 1:
                    temp[ii, jj] = -2.0
                    temp[ii, jj-1] = 1.0
                    temp[ii, jj+1] = 1.0
                elif jj == self.nzsolve - 2:
                    temp[ii, jj] = -2.0
                    temp[ii, jj+1] = 1.0
                    temp[ii, jj-1] = 1.0
                elif ii == 0:
                    temp[ii, jj] = 1.0
                    temp[-3, jj] = -1.0
                elif ii == self.nxsolve - 1:
                    temp[ii, jj] = 1.0
                    temp[2, jj] = -1.0
                else:
                    temp[ii, jj] = -4.0
                    temp[ii+1, jj] = 1.0
                    temp[ii-1, jj] = 1.0
                    temp[ii, jj-1] = 1.0
                    temp[ii, jj+1] = 1.0

                A[kk] = temp.flatten()
                kk += 1

        A = csc_matrix(A, dtype=np.float32)
        self.lu = sla.splu(A)

    def _run_solve(self):
        """Function run on every step to perform the required steps to solve
        Poisson's equation."""
        if not mwxrun.initialized:
            return

        # get rho from WarpX
        self.rho_data = mwxrun.get_gathered_rho_grid()[:,:,0]
        # run superLU solver to get phi
        self.solve()
        # write phi to WarpX
        mwxrun.set_phi_grid(self.phi)

    def solve(self):
        """The solution step. Includes getting the boundary potentials and
        calculating phi from rho."""

        left_voltage = self.left_voltage()
        right_voltage = self.right_voltage()

        rho = -self.rho_data / constants.ep0

        # Construct b vector
        nx, nz = np.shape(rho)
        source = np.zeros((nx+2, nz), dtype=np.float32)
        source[1:-1,:] = rho * self.dx**2

        source[:,0] = left_voltage
        source[:,-1] = right_voltage

        # Construct b vector
        b = source.flatten()

        flat_phi = self.lu.solve(b)
        self.phi[:, self.nzguardphi:-self.nzguardphi] = (
            flat_phi.reshape(np.shape(source))
        )

        self.phi[:,:self.nzguardphi] = left_voltage
        self.phi[:,-self.nzguardphi:] = right_voltage

        # the electrostatic solver in WarpX keeps the ghost cell values as 0
        self.phi[:self.nxguardphi,:] = 0
        self.phi[-self.nxguardphi:,:] = 0


class DummyPoissonSolver(ElectrostaticSolver):

    def __init__(self, grid, **kwargs):
        """Dummy solver to allow us to effectively turn off the field solve
        step.

        Arguments:
            grid (picmi.Cartesian1DGrid, picmi.Cartesian2DGrid or
                picmi.CylindricalGrid): Instance of the grid on which the
                dummy solver will be installed.
        """
        super(DummyPoissonSolver, self).__init__(
            grid=grid, method=kwargs.pop('method', 'Multigrid'),
            required_precision=1, **kwargs
        )

        callbacks.installpoissonsolver(self._run_solve)

    def _run_solve(self):
        pass
