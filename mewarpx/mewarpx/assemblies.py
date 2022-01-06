"""
Assembly implementations.
"""
import collections
import logging

import numpy as np
from pywarpx import picmi

from mewarpx.mwxrun import mwxrun
from mewarpx.utils_store import parallel_util
from mewarpx.utils_store.appendablearray import AppendableArray

# Get module-level logger
logger = logging.getLogger(__name__)

class Assembly(object):

    """An assembly represents any shape in the simulation; usually a conductor.

    While V, T, and WF are required, specific use cases may allow None to be
    used for these fields.
    """

    # This is overridden if a diagnostic is installed to record scraped
    # current.
    scraper_diag = None

    # fields is used by the diags.FluxInjectorDiag to know what to write to
    # the CSV file. It can be overridden by child classes, but is not currently
    # adjustable by the user.

    # IF CHANGING THIS, CHANGE IN self.record_scrapedparticles() AS WELL.
    fields = ['t', 'step', 'species_id', 'V_e', 'n', 'q', 'E_total']

    def __init__(self, V, T, WF, name):
        """Basic initialization.

        Arguments:
            V (float): Voltage (V)
            T (float): Temperature (K)
            WF (float): Work function (eV)
            name (str): Assembly name
        """
        self.V = V
        self.T = T
        self.WF = WF
        self.name = name

    def check_geom(self):
        """Throw an error if the simulation geometry is unsupported by the
        Assembly.
        """
        if mwxrun.geom_str not in self.geoms:
            raise ValueError(
                f"{geom} geometry not supported by this Assembly")

    def getvoltage(self):
        """Allows for time-dependent implementations to override this."""
        return mwxrun.eval_expression_t(self.V)

    def getvoltage_e(self):
        """Allows for time-dependent implementations to override this."""
        return self.getvoltage() + self.WF

    def _install_in_simulation(self):
        """Function to handle installation of embedded boundaries in the
        simulation.
        Care is taken to have the possibility to install multiple embedded
        conductors (EBs). This is done by making the overall implicit function
        that describe the EBs use the maximum of the individual EB's implicit
        functions - this works since for each individual implicit function -1
        means outside the boundary, 0 means on the boundary edge and 1 means
        inside the boundary.
        Furthermore, the potential for the embedded boundary is also constructed
        with support for multiple embedded boundaries by adding a check for
        which embedded boundary is considered when assigning the potential.
        """
        if mwxrun.simulation.embedded_boundary is not None:

            # wrap the current EB potential in an if statement with the
            # new implicit function
            old_str = mwxrun.simulation.embedded_boundary.potential.strip('"')
            mwxrun.simulation.embedded_boundary.potential = (
                f'"if({self.implicit_function}>0,{self.V},{old_str})"'
            )

            # add a nested max() statement with the current implicit function
            # to add the new embedded boundary
            old_str = mwxrun.simulation.embedded_boundary.implicit_function.strip('"')
            mwxrun.simulation.embedded_boundary.implicit_function = (
                f'"max({old_str},{self.implicit_function})"'
            )
        else:
            mwxrun.simulation.embedded_boundary = picmi.EmbeddedBoundary(
                implicit_function=f'"{self.implicit_function}"',
                potential=f'"{self.V}"'
            )

    def init_scrapedparticles(self, fieldlist):
        """Set up the scraped particles array. Call before
        append_scrapedparticles.

        Arguments:
            fieldlist (list): List of string titles for the fields. Order is
                important; it must match the order for future particle appends
                that are made.
        """
        self._scrapedparticles_fields = fieldlist
        self._scrapedparticles_data = AppendableArray(
            typecode='d', unitshape=[len(fieldlist)])

    def record_scrapedparticles(self):
        """Handles transforming raw particle information from the WarpX
        scraped particle buffer to the information used to record particles as
        a function of time.

        Note:
            Assumes the fixed form of fields given in Assembly().  Doesn't
            check since this is called many times.

        Note:
            The total charge scraped and energy of the particles scraped
            is multiplied by -1 since these quantities are leaving the system.
        """

        # skip conductors that don't have a label to get scraped particles with
        if not hasattr(self, 'scraper_label'):
            logger.warning(f"Assembly {self.name} doesn't have a scraper label")
            return

        # loop over species and get the scraped particle data from the buffer
        for species in mwxrun.simulation.species:
            data = np.zeros(7)
            data[0] = mwxrun.get_t()
            data[1] = mwxrun.get_it()
            data[2] = species.species_number
            data[3] = self.getvoltage_e()

            # When pre-seeding a simulation with plasma we inject particles over
            # embedded boundaries which causes the first scraping step to
            # show very large currents. For this reason we skip that first step
            # but note that we inject after the first step so we need to skip
            # scraping step 2.
            if data[1] == 2:
                self.append_scrapedparticles(data)
                continue

            empty = True
            idx_list = []

            # get the number of particles in the buffer - this is primarily
            # to avoid trying to access the buffer if it is not defined
            # which causes a segfault
            buffer_count = mwxrun.sim_ext.get_particle_boundary_buffer_size(
                species.name, self.scraper_label
            )
            # logger.info(f"{self.name} scraped {buffer_count} {species.name}")

            if buffer_count > 0:
                # get the timesteps at which particles were scraped
                comp_steps = mwxrun.sim_ext.get_particle_boundary_buffer(
                    species.name, self.scraper_label, "step_scraped", mwxrun.lev
                )
                # get the particles that were scraped in this timestep
                for arr in comp_steps:
                    idx_list.append(np.where(arr == mwxrun.get_it())[0])
                    if len(idx_list[-1]) != 0:
                        empty = False

                # sort the particles appropriately if this is an eb
                if not empty and self.scraper_label == 'eb':
                    temp_idx_list = []
                    structs = mwxrun.sim_ext.get_particle_boundary_buffer_structs(
                        species.name, self.scraper_label, mwxrun.lev
                    )

                    if mwxrun.geom_str == 'XZ':
                        xpos = [struct['x'] for struct in structs]
                        ypos = [struct['y'] for struct in structs]
                        zpos = [struct['y'] for struct in structs]
                    elif mwxrun.geom_str == 'XYZ':
                        xpos = [struct['x'] for struct in structs]
                        ypos = [struct['y'] for struct in structs]
                        zpos = [struct['z'] for struct in structs]
                    elif mwxrun.geom_str == 'RZ':
                        xpos = [struct['x'] for struct in structs]
                        ypos = [np.zeros(len(struct['y'])) for struct in structs]
                        zpos = [struct['y'] for struct in structs]
                    else:
                        raise NotImplementedError(
                            f"Scraping not implemented for {mwxrun.geom_str}."
                        )

                    for i in range(len(idx_list)):
                        is_inside = self.isinside(
                            xpos[i][idx_list[i]], ypos[i][idx_list[i]],
                            zpos[i][idx_list[i]]
                        )
                        temp_idx_list.append(idx_list[i][np.where(is_inside)])

                        # set the scraped timestep to -1 for particles in this
                        # EB so that they are not considered again
                        comp_steps[i][idx_list[i][np.where(is_inside)]] = -1

                    idx_list = temp_idx_list

            if not empty:
                data[4] = sum(np.size(idx) for idx in idx_list)
                w_arrays = mwxrun.sim_ext.get_particle_boundary_buffer(
                    species.name, self.scraper_label, "w", mwxrun.lev
                )
                data[5] = -species.sq * sum(np.sum(w_arrays[i][idx_list[i]])
                     for i in range(len(idx_list))
                )
                E_arrays = mwxrun.sim_ext.get_particle_boundary_buffer(
                    species.name, self.scraper_label, "E_total", mwxrun.lev
                )
                data[6] = -sum(np.sum(E_arrays[i][idx_list[i]])
                     for i in range(len(idx_list))
                )

            self.append_scrapedparticles(data)

    def append_scrapedparticles(self, data):
        """Append one or more lines of scraped particles data.

        Arguments:
            data (np.ndarray): Array of shape (m) or (n, m) where m is the
                number of fields and n is the number of rows of data to append.
        """
        self._scrapedparticles_data.append(data)

    def get_scrapedparticles(self, clear=False):
        """Retrieve a copy of scrapedparticles data.

        Arguments:
            clear (bool): If True, clear the particle data rows entered (field
                names are still initialized as before). Default False.

        Returns:
            scrapedparticles_dict (collections.OrderedDict): Keys are the
                originally passed field strings for lost particles. Values are
                an (n)-shape numpy array for each field.
        """
        lpdata = self._scrapedparticles_data.data()

        # Sum all except t/step/jsid/V_e from all processors
        lpdata[:,4:] = parallel_util.parallelsum(np.array(lpdata[:,4:]))

        lpdict = collections.OrderedDict(
            [(fieldname, np.array(lpdata[:, ii], copy=True))
             for ii, fieldname in enumerate(self._scrapedparticles_fields)])

        if clear:
            self._scrapedparticles_data.cleardata()

        return lpdict


class ZPlane(Assembly):

    """A semi-infinite plane."""
    geoms = ['RZ', 'X', 'XZ', 'XYZ']

    def __init__(self, z, zsign, V, T, WF, name):
        """Basic initialization.

        Arguments:
            z (float): The edge of the semi-infinite plane (m)
            zsign (int): =1 to extend from z to +inf, or =-1 to extend from
                -inf to z.
            V (float): Voltage (V)
            T (float): Temperature (K)
            WF (float): Work function (eV)
            name (str): Assembly name
        """
        super(ZPlane, self).__init__(V=V, T=T, WF=WF, name=name)

        self.z = z

        self.zsign = int(round(zsign))
        if self.zsign not in [-1, 1]:
            raise ValueError(f"self.zsign = {self.zsign} is not -1 or 1.")


class Cathode(ZPlane):
    """A basic wrapper to define a semi-infinite plane for the cathode."""

    def __init__(self, V, T, WF):
        super(Cathode, self).__init__(
            z=0, zsign=-1, V=V, T=T, WF=WF, name="Cathode"
        )
        # set solver boundary potential
        mwxrun.grid.potential_zmin = self.V
        # set label to correctly grab scraped particles from the buffer
        self.scraper_label = 'z_lo'

class Anode(ZPlane):
    """A basic wrapper to define a semi-infinite plane for the anode."""

    def __init__(self, z, V, T, WF):
        super(Anode, self).__init__(
            z=z, zsign=1, V=V, T=T, WF=WF, name="Anode"
        )
        # set solver boundary potential
        mwxrun.grid.potential_zmax = self.V
        # set label to correctly grab scraped particles from the buffer
        self.scraper_label = 'z_hi'

class InfCylinderY(Assembly):
    """An infinitely long Cylinder pointing in the y-direction."""
    geoms = ['XZ', 'XYZ']

    def __init__(self, center_x, center_z, radius, V, T, WF, name,
                 install_in_simulation=True):
        """Basic initialization.

        Arguments:
            center_x (float): The x-coordinates of the center of the cylinder.
                Coordinates are in (m)
            center_z (float): The z-coordinates of the center of the cylinder.
                Coordinates are in (m)
            radius (float): The radius of the cylinder (m)
            V (float): Voltage (V)
            T (float): Temperature (K)
            WF (float): Work function (eV)
            name (str): Assembly name
            install_in_simulation (bool): If True and the Assembly is an
                embedded boundary it will be included in the WarpX simulation
        """
        super(InfCylinderY, self).__init__(V=V, T=T, WF=WF, name=name)
        self.center_x = center_x
        self.center_z = center_z
        self.radius = radius
        # negative means outside of object
        self.implicit_function = (
            f"-((x-{self.center_x})**2+(z-{self.center_z})**2-{self.radius}**2)"
        )

        self.scraper_label = 'eb'

        if install_in_simulation:
            self._install_in_simulation()

    def isinside(self, X, Y, Z, aura=0):
        """
        Calculates which grid tiles are within the cylinder.

        Arguments:
            X (np.ndarray): array of x coordinates of flattened grid.
            Y (np.ndarray): array of y coordinates of flattened grid.
            Z (np.ndarray): array of z coordinates of flattened grid.
            aura (float): extra space around the conductor that is considered
                inside. Useful for small, thin conductors that don't overlap any
                grid points. In units of meters.

        Returns:
            result (np.ndarray): array of flattened grid where all tiles
                inside the cylinder are 1, and other tiles are 0.
        """
        dist_to_center = (X - self.center_x)**2 + (Z - self.center_z)**2
        boundary = (self.radius + aura)**2
        result = np.where(dist_to_center <= boundary, 1, 0)

        return result

    def calculatenormal(self, px, py, pz):
        """
        Calculates Normal of particle inside/outside of conductor to nearest
        surface of conductor

        Arguments:
            px (np.ndarray): x-coordinate of particle in meters.
            py (np.ndarray): y-coordinate of particle in meters.
            pz (np.ndarray): z-coordinate of particle in meters.

        Returns:
            nhat (np.ndarray): Normal of particles of conductor to nearest
                surface of conductor.
        """
        # distance from center of cylinder
        dist = np.sqrt((px - self.center_x)**2 + (pz - self.center_z)**2)
        nhat = np.zeros([3, len(px)])
        nhat[0, :] = (px - self.center_x) / dist
        nhat[2, :] = (pz - self.center_z) / dist
        return nhat


class CylinderZ(Assembly):
    """A Cylinder pointing in the z-direction with center along the x and y
    origin line (to support RZ)."""
    geoms = ['RZ']

    def __init__(self, V, T, WF, name, r_outer, r_inner=0.0, zmin=None,
                 zmax=None):
        """Basic initialization.

        Arguments:
            V (float): Voltage (V)
            T (float): Temperature (K)
            WF (float): Work function (eV)
            name (str): Assembly name
            r_outer (float): The outer radius of the cylinder (m)
            r_inner (float): The inner radius of the cylinder (m)
            zmin (float): Lower z limit of the cylinder (m). Defaults to
                mwxrun.zmin.
            zmax (float): Upper z limit of the cylinder (m). Defaults to
                mwxrun.zmax.
        """
        super(CylinderZ, self).__init__(V=V, T=T, WF=WF, name=name)
        self.r_inner = r_inner
        self.r_outer = r_outer
        self.r_center = (self.r_inner + self.r_outer) / 2.0

        self.zmin = zmin
        if self.zmin is None:
            self.zmin = mwxrun.zmin
        self.zmax = zmax
        if self.zmax is None:
            self.zmax = mwxrun.zmax

        # check if z-limits span the full simulation domain
        # if np.close(self.zmax, mwxrun.zmax) and np.close(self.zmin, mwxrun.zmin):
        infinite_cyl = np.allclose(
            [self.zmax - mwxrun.zmax, self.zmin - mwxrun.zmin], 0.)

        self.center_x = 0.0
        self.center_y = 0.0

        # sanity check
        if self.r_outer == 0:
            raise AttributeError('Cannot have a cylinder with 0 outer radius.')

        # check if the cylinder forms the simulation boundary
        if np.isclose(self.r_inner, mwxrun.rmax):
            if not infinite_cyl:
                raise AttributeError(
                    "CylinderZ on the simulation boundary must span the full "
                    "z domain."
                )
            # set solver boundary potential
            mwxrun.grid.potential_xmax = self.V
            # set label to correctly grab scraped particles from the buffer
            self.scraper_label = 'x_hi'
        elif np.isclose(self.r_outer, mwxrun.rmin):
            if not infinite_cyl:
                raise AttributeError(
                    "CylinderZ on the simulation boundary must span the full "
                    "z domain."
                )
            # set solver boundary potential
            mwxrun.grid.potential_xmin = self.V
            # set label to correctly grab scraped particles from the buffer
            self.scraper_label = 'x_lo'
        else:
            # handle the EB case
            # a negative value of the implicit function means outside of object
            if infinite_cyl:
                # no z dependence since the cylinder is infinitely long
                self.implicit_function = (
                    f"-(abs(x-{self.r_center})-{self.r_outer - self.r_center})"
                )
            else:
                self.implicit_function = (
                    f"if(z>{self.zmin} and z<{self.zmax}, "
                    f"-(abs(x-{self.r_center})-{self.r_outer - self.r_center}),"
                    "-1.0)"
                )
            # set label to correctly grab scraped particles from the buffer
            self.scraper_label = 'eb'
            # install the EB in the simulation
            self._install_in_simulation()

    def isinside(self, X, Y, Z, aura=0):
        """
        Calculates which grid tiles are within the cylinder.

        Arguments:
            X (np.ndarray): array of x coordinates of flattened grid.
            Y (np.ndarray): array of y coordinates of flattened grid.
            Z (np.ndarray): array of z coordinates of flattened grid.
            aura (float): extra space around the conductor that is considered
                inside. Useful for small, thin conductors that don't overlap any
                grid points. In units of meters.

        Returns:
            result (np.ndarray): array of flattened grid where all tiles
                inside the cylinder are 1, and other tiles are 0.
        """
        dist_to_center = np.sqrt(
            (X - self.center_x)**2 + (Y - self.center_y)**2
        )
        dr = self.r_outer + aura - self.r_center
        result = np.where(abs(dist_to_center - self.r_center) <= dr, 1, 0)

        return result

    def calculatenormal(self, px, py, pz):
        """
        Calculates Normal of particle inside/outside of conductor to nearest
        surface of conductor

        Arguments:
            px (np.ndarray): x-coordinate of particle in meters.
            py (np.ndarray): y-coordinate of particle in meters.
            pz (np.ndarray): z-coordinate of particle in meters.

        Returns:
            nhat (np.ndarray): Normal of particles of conductor to nearest
                surface of conductor.
        """
        # distance from center of cylinder
        dist = np.sqrt((px - self.center_x)**2 + (py - self.center_y)**2)
        nhat = np.zeros([3, len(px)])
        nhat[0, :] = (px - self.center_x) / dist
        nhat[2, :] = (pz - self.center_z) / dist

        # Flip the normal vector for points inside the cylinder inner wall,
        # or inside the cylinder assembly but closer to the outer wall than the inner.
        idx = np.where(
            np.logical_or(
                (dist < self.r_inner),
                ((dist > self.r_center) & (dist < self.router))
            )
        )
        nhat[:,idx] *= -1

        return nhat


class Rectangle(Assembly):
    """A rectangular prism infinite in y."""
    geoms = ['XZ', 'XYZ']

    def __init__(self, center_x, center_z, length_x, length_z, V, T, WF, name,
                 install_in_simulation=True):
        """Basic initialization.

        Arguments:
            center_x (float): The x-coordinates of the center of the rectangle.
                Coordinates are in (m)
            center_z (float): The z-coordinates of the center of the rectangle.
                Coordinates are in (m)
            length_x (float): The length the rectangle in the x direction (m)
            length_z (float): The length the rectangle in the z direction (m)
            V (float): Voltage (V)
            T (float): Temperature (K)
            WF (float): Work function (eV)
            name (str): Assembly name
            install_in_simulation (bool): If True and the Assembly is an
                embedded boundary it will be included in the WarpX simulation
        """
        super(Rectangle, self).__init__(V=V, T=T, WF=WF, name=name)
        self.center_x = float(center_x)
        self.center_z = float(center_z)
        self.length_x = float(length_x)
        self.length_z = float(length_z)
        self.xmin = float(center_x - length_x/2)
        self.xmax = float(center_x + length_x/2)
        self.zmin = float(center_z - length_z/2)
        self.zmax = float(center_z + length_z/2)

        self.scaled_h = (
            2.0 * max(self.length_x, self.length_z)
            / min(self.length_x, self.length_z)
        )

        # the regions change depending on whether x or z is longer, so we need
        # to adjust the preset normals for these regions depending on the x and
        # z lengths
        if self.length_x <= self.length_z:
            self.region_normals = np.array([
                (0, -1), (1, 0), (0, 1), (-1, 0)
            ])
        else:
            self.region_normals = np.array([
                (-1, 0), (0, -1), (1, 0), (0, 1)
            ])

        self.transformation_matrix = self._get_transformation_matrix()

        # Negative means outside of object
        self.implicit_function = (
            f"-max(max(x-({self.xmax}),({self.xmin})-x),"
            f"max(z-({self.zmax}),({self.zmin})-z))"
        )
        self.scraper_label = 'eb'

        if install_in_simulation:
            self._install_in_simulation()

    def calculatenormal(self, px, py, pz):
        """
        Calculates Normal of particle inside/outside of conductor to nearest
        surface of conductor. Nearest surface of conductor is determined by the
        region of the transformed rectangle that the transformed particle
        belongs to.

        The 4 regions in order from 0 - 3 are:
            0: bottom portion of transformed rectangle and below transformed
               rectangle (-y)
            1: right portion of transformed rectangle and to the right of
               transformed rectangle (+x)
            2: top portion of transformed rectangle and above transformed
               rectangle (+y)
            3: left portion of transformed rectangle and to the left of
               transformed rectangle (-x)

        Arguments:
            px (np.ndarray): x-coordinate of particle in meters.
            py (np.ndarray): y-coordinate of particle in meters.
            pz (np.ndarray): z-coordinate of particle in meters.

        Returns:
            nhat (np.ndarray): Normal of particles of conductor to nearest
                surface of conductor.
        """
        arr_length = px.shape[0]
        points = np.zeros((arr_length, 2))
        points[:, 0] = px
        points[:, 1] = pz
        transformed_points = self._transform_coordinates(points)
        idx = self._sort_particles(transformed_points)
        normals = self.region_normals[idx]
        nhat = np.zeros((3, (normals.shape[0])))
        nhat[0, :] = normals[:, 0]
        nhat[2, :] = normals[:, 1]
        return nhat

    def _get_transformation_matrix(self):
        """Tranform1 is used to move the rectangle to the appropriate location
        in the plane and scale it to the appropriate size. The rectangle is
        placed such that the longest side is along the z-axis and the bottom
        right corner is at (1, -1). This placement makes it simple to pick out
        positions that are in regions 0 or 1 as well as positions in region 4
        or 5.
        """
        # first transpose the box to the origin
        transpose1 = np.identity(3)
        transpose1[0, 2] = (-self.center_x + self.length_x / 2.0)
        transpose1[1, 2] = (-self.center_z + self.length_z / 2.0)

        # next scale the axes to make zone 0 equilateral with side length 1
        l = min(self.length_x, self.length_z)
        scale = np.identity(3)
        scale[0, 0] = 2.0 / l
        scale[1, 1] = 2.0 / l

        # next transpose again so the center is at the origin
        transpose2 = np.identity(3)
        transpose2[0, 2] = -1
        transpose2[1, 2] = -1

        # rotate by 90 deg if the length_z is smallest
        rotate = np.identity(3)
        if self.length_z < self.length_x:
            rotate[0,0] = np.cos(np.pi/2)
            rotate[0,1] = -np.sin(np.pi/2)
            rotate[1,0] = np.sin(np.pi/2)
            rotate[1,1] = np.cos(np.pi/2)

        transformation_matrix = np.dot(
            np.dot(rotate, transpose2),
            np.dot(scale, transpose1)
        )
        return transformation_matrix

    def _transform_coordinates(self, points_in):
        """Transforms the coordinates of the particles using the
        transformation matrix of the rectangle"""
        points = np.ones((len(points_in), 3))
        points[:, 0] = points_in[:, 0]
        points[:, 1] = points_in[:, 1]

        points = points.dot(self.transformation_matrix.T)

        return points[:, 0:2]

    def _sort_particles(self, points):
        """Finds the regions of the rectangle that each point belongs to"""
        # The checks below isolate the region in which the given position lies.
        # c0 and c2 are True iff a particle is in region 0 or 2, respectively
        # not_c1 indicates a particle may be in regions 0, 2, or 3.
        c0 = np.array(points[:, 1] < -abs(points[:, 0]), dtype=int)
        c2 = (
            np.array(points[:, 1] - self.scaled_h + 2.0
            > abs(points[:, 0]), dtype=int)
        )
        not_c1 = np.array(points[:, 0] <= 0, dtype=int)
        # the booleans below form bit values to build up the index of the
        # region in which a given position lies.
        # bit2 is True if in region 2 or 3.
        bit2 = c2 | (not_c1 & np.logical_not(c0))
        # bit1 is True if in region 1 or 3; equivalently, if it's not in 0 or 2.
        bit1 = np.logical_not(c0 | c2)
        idx = 2*bit2 + bit1

        return idx

    def isinside(self, X, Y, Z, aura=0):
        """
        Calculates which grid tiles are within the rectangle.

        Arguments:
            X (np.ndarray): array of x coordinates of flattened grid.
            Y (np.ndarray): array of y coordinates of flattened grid.
            Z (np.ndarray): array of z coordinates of flattened grid.
            aura (float): extra space around the conductor that is considered
                inside. Useful for small, thin conductors that don't overlap any
                grid points. In units of meters.

        Returns:
            result (np.ndarray): array of flattened grid where all tiles
                inside the rectangle are 1, and other tiles are 0.
        """

        X_in_bounds = np.where(
            np.maximum(X-self.xmax-aura, self.xmin-aura-X) <= 0, 1, 0)
        Z_in_bounds = np.where(
            np.maximum(Z-self.zmax-aura, self.zmin-aura-Z) <= 0, 1, 0)
        result = np.where(X_in_bounds + Z_in_bounds > 1, 1, 0)

        return result
