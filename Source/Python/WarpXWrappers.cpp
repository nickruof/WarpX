/* Copyright 2019 Andrew Myers, Axel Huebl, David Grote
 * Luca Fedeli, Maxence Thevenet, Remi Lehe
 * Weiqun Zhang
 *
 * This file is part of WarpX.
 *
 * License: BSD-3-Clause-LBNL
 */
#include "BoundaryConditions/PML.H"
#include "Initialization/WarpXAMReXInit.H"
#include "Particles/Gather/ScalarFieldGather.H"
#include "Particles/MultiParticleContainer.H"
#include "Particles/ParticleBoundaryBuffer.H"
#include "Particles/WarpXParticleContainer.H"
#include "Utils/WarpXUtil.H"
#include "WarpX.H"
#include "WarpXWrappers.H"
#include "WarpX_py.H"

#include <AMReX.H>
#include <AMReX_ArrayOfStructs.H>
#include <AMReX_Box.H>
#include <AMReX_FArrayBox.H>
#include <AMReX_FabArray.H>
#include <AMReX_Geometry.H>
#include <AMReX_GpuControl.H>
#include <AMReX_IndexType.H>
#include <AMReX_IntVect.H>
#include <AMReX_MFIter.H>
#include <AMReX_MultiFab.H>
#include <AMReX_PODVector.H>
#include <AMReX_ParIter.H>
#include <AMReX_Particles.H>
#include <AMReX_StructOfArrays.H>

#include <array>
#include <cstdlib>

namespace
{
    amrex::Real** getMultiFabPointers(const amrex::MultiFab& mf, int *num_boxes, int *ncomps, int **ngrowvect, int **shapes)
    {
        *ncomps = mf.nComp();
        *num_boxes = mf.local_size();
        int shapesize = AMREX_SPACEDIM;
        *ngrowvect = static_cast<int*>(malloc(sizeof(int)*shapesize));
        for (int j = 0; j < AMREX_SPACEDIM; ++j) {
            (*ngrowvect)[j] = mf.nGrow(j);
        }
        if (mf.nComp() > 1) shapesize += 1;
        *shapes = static_cast<int*>(malloc(sizeof(int)*shapesize * (*num_boxes)));
        auto data =
            static_cast<amrex::Real**>(malloc((*num_boxes) * sizeof(amrex::Real*)));

#ifdef AMREX_USE_OMP
#pragma omp parallel if (amrex::Gpu::notInLaunchRegion())
#endif
        for ( amrex::MFIter mfi(mf, false); mfi.isValid(); ++mfi ) {
            int i = mfi.LocalIndex();
            data[i] = (amrex::Real*) mf[mfi].dataPtr();
            for (int j = 0; j < AMREX_SPACEDIM; ++j) {
                (*shapes)[shapesize*i+j] = mf[mfi].box().length(j);
            }
            if (mf.nComp() > 1) (*shapes)[shapesize*i+AMREX_SPACEDIM] = mf.nComp();
        }
        return data;
    }
    int* getMultiFabLoVects(const amrex::MultiFab& mf, int *num_boxes, int **ngrowvect)
    {
        int shapesize = AMREX_SPACEDIM;
        *ngrowvect = static_cast<int*>(malloc(sizeof(int)*shapesize));
        for (int j = 0; j < AMREX_SPACEDIM; ++j) {
            (*ngrowvect)[j] = mf.nGrow(j);
        }
        *num_boxes = mf.local_size();
        int *loVects = (int*) malloc((*num_boxes)*AMREX_SPACEDIM * sizeof(int));

        int i = 0;
        for ( amrex::MFIter mfi(mf, false); mfi.isValid(); ++mfi, ++i ) {
            const int* loVect = mf[mfi].loVect();
            for (int j = 0; j < AMREX_SPACEDIM; ++j) {
                loVects[AMREX_SPACEDIM*i+j] = loVect[j];
            }
        }
        return loVects;
    }
    // Copy the nodal flag data and return the copy:
    // the nodal flag data should not be modifiable from Python.
    int* getFieldNodalFlagData ( const amrex::MultiFab* mf )
    {
        if (mf == nullptr) return nullptr;
        const amrex::IntVect nodal_flag( mf->ixType().toIntVect() );
        int *nodal_flag_data = (int*) malloc(AMREX_SPACEDIM * sizeof(int));

        constexpr int NODE = amrex::IndexType::NODE;

        for (int i=0 ; i < AMREX_SPACEDIM ; i++) {
            nodal_flag_data[i] = (nodal_flag[i] == NODE ? 1 : 0);
        }
        return nodal_flag_data;
    }
}

extern "C"
{

    int warpx_Real_size()
    {
        return (int)sizeof(amrex::Real);
    }

    int warpx_ParticleReal_size()
    {
        return (int)sizeof(amrex::ParticleReal);
    }

    int warpx_nSpecies()
    {
        const auto & mypc = WarpX::GetInstance().GetPartContainer();
        return mypc.nSpecies();
    }

    bool warpx_use_fdtd_nci_corr()
    {
        return WarpX::use_fdtd_nci_corr;
    }

    int warpx_galerkin_interpolation()
    {
        return WarpX::galerkin_interpolation;
    }

    int warpx_nComps()
    {
        return PIdx::nattribs;
    }

    int warpx_nCompsSpecies(const char* char_species_name)
    {
        auto & mypc = WarpX::GetInstance().GetPartContainer();
        const std::string species_name(char_species_name);
        auto & myspc = mypc.GetParticleContainerFromName(species_name);
        return myspc.NumRealComps();
    }

    int warpx_SpaceDim()
    {
        return AMREX_SPACEDIM;
    }

    void amrex_init (int argc, char* argv[])
    {
        warpx_amrex_init(argc, argv);
    }

    void amrex_init_with_inited_mpi (int argc, char* argv[], MPI_Comm mpicomm)
    {
        warpx_amrex_init(argc, argv, true, mpicomm);
    }

    void amrex_finalize (int /*finalize_mpi*/)
    {
        amrex::Finalize();
    }

    void warpx_init ()
    {
        WarpX& warpx = WarpX::GetInstance();
        warpx.InitData();
        if (warpx_py_afterinit) warpx_py_afterinit();
        if (warpx_py_particleloader) warpx_py_particleloader();
    }

    void warpx_finalize ()
    {
        WarpX::ResetInstance();
    }

    void warpx_set_callback_py_afterinit (WARPX_CALLBACK_PY_FUNC_0 callback)
    {
        warpx_py_afterinit = callback;
    }
    void warpx_set_callback_py_beforeEsolve (WARPX_CALLBACK_PY_FUNC_0 callback)
    {
        warpx_py_beforeEsolve = callback;
    }
    void warpx_set_callback_py_poissonsolver (WARPX_CALLBACK_PY_FUNC_0 callback)
    {
        warpx_py_poissonsolver = callback;
    }
    void warpx_set_callback_py_afterEsolve (WARPX_CALLBACK_PY_FUNC_0 callback)
    {
        warpx_py_afterEsolve = callback;
    }
    void warpx_set_callback_py_beforedeposition (WARPX_CALLBACK_PY_FUNC_0 callback)
    {
        warpx_py_beforedeposition = callback;
    }
    void warpx_set_callback_py_afterdeposition (WARPX_CALLBACK_PY_FUNC_0 callback)
    {
        warpx_py_afterdeposition = callback;
    }
    void warpx_set_callback_py_particlescraper (WARPX_CALLBACK_PY_FUNC_0 callback)
    {
        warpx_py_particlescraper = callback;
    }
    void warpx_set_callback_py_particleloader (WARPX_CALLBACK_PY_FUNC_0 callback)
    {
        warpx_py_particleloader = callback;
    }
    void warpx_set_callback_py_beforestep (WARPX_CALLBACK_PY_FUNC_0 callback)
    {
        warpx_py_beforestep = callback;
    }
    void warpx_set_callback_py_afterstep (WARPX_CALLBACK_PY_FUNC_0 callback)
    {
        warpx_py_afterstep = callback;
    }
    void warpx_set_callback_py_afterrestart (WARPX_CALLBACK_PY_FUNC_0 callback)
    {
        warpx_py_afterrestart = callback;
    }
    void warpx_set_callback_py_particleinjection (WARPX_CALLBACK_PY_FUNC_0 callback)
    {
        warpx_py_particleinjection = callback;
    }
    void warpx_set_callback_py_appliedfields (WARPX_CALLBACK_PY_FUNC_0 callback)
    {
        warpx_py_appliedfields = callback;
    }

    void warpx_evolve (int numsteps)
    {
        WarpX& warpx = WarpX::GetInstance();
        warpx.Evolve(numsteps);
    }

    void warpx_addNParticles(
        const char* char_species_name, int lenx, amrex::ParticleReal const * x,
        amrex::ParticleReal const * y, amrex::ParticleReal const * z,
        amrex::ParticleReal const * vx, amrex::ParticleReal const * vy,
        amrex::ParticleReal const * vz, int nattr,
        amrex::ParticleReal const * attr, int uniqueparticles)
    {
        auto & mypc = WarpX::GetInstance().GetPartContainer();
        const std::string species_name(char_species_name);
        auto & myspc = mypc.GetParticleContainerFromName(species_name);
        const int lev = 0;
        myspc.AddNParticles(lev, lenx, x, y, z, vx, vy, vz, nattr, attr, uniqueparticles);
    }

    void warpx_ConvertLabParamsToBoost()
    {
      ConvertLabParamsToBoost();
    }

    void warpx_ReadBCParams()
    {
      ReadBCParams();
    }

    void warpx_CheckGriddingForRZSpectral()
    {
      CheckGriddingForRZSpectral();
    }

    amrex::Real warpx_getProbLo(int dir)
    {
      WarpX& warpx = WarpX::GetInstance();
      const amrex::Geometry& geom = warpx.Geom(0);
      return geom.ProbLo(dir);
    }

    amrex::Real warpx_getProbHi(int dir)
    {
      WarpX& warpx = WarpX::GetInstance();
      const amrex::Geometry& geom = warpx.Geom(0);
      return geom.ProbHi(dir);
    }

    amrex::Real warpx_getCellSize(int dir, int lev) {
        const std::array<amrex::Real,3>& dx = WarpX::CellSize(lev);
        return dx[dir];
    }

    long warpx_getNumParticles(const char* char_species_name) {
        const auto & mypc = WarpX::GetInstance().GetPartContainer();
        const std::string species_name(char_species_name);
        auto & myspc = mypc.GetParticleContainerFromName(species_name);
        return myspc.TotalNumberOfParticles();
    }

#define WARPX_GET_FIELD(FIELD, GETTER) \
    amrex::Real** FIELD(int lev, int direction, \
                        int *return_size, int *ncomps, int **ngrowvect, int **shapes) { \
        auto * mf = GETTER(lev, direction); \
        if (mf != nullptr) { \
            return getMultiFabPointers(*mf, return_size, ncomps, ngrowvect, shapes); \
        } else { \
            return nullptr; \
        } \
    }

#define WARPX_GET_LOVECTS(FIELD, GETTER) \
    int* FIELD(int lev, int direction, \
               int *return_size, int **ngrowvect) { \
        auto * mf = GETTER(lev, direction); \
        if (mf != nullptr) { \
            return getMultiFabLoVects(*mf, return_size, ngrowvect); \
        } else { \
            return nullptr; \
        } \
    }

    WARPX_GET_FIELD(warpx_getEfield, WarpX::GetInstance().get_pointer_Efield_aux)
    WARPX_GET_FIELD(warpx_getEfieldCP, WarpX::GetInstance().get_pointer_Efield_cp)
    WARPX_GET_FIELD(warpx_getEfieldFP, WarpX::GetInstance().get_pointer_Efield_fp)

    WARPX_GET_FIELD(warpx_getBfield, WarpX::GetInstance().get_pointer_Bfield_aux)
    WARPX_GET_FIELD(warpx_getBfieldCP, WarpX::GetInstance().get_pointer_Bfield_cp)
    WARPX_GET_FIELD(warpx_getBfieldFP, WarpX::GetInstance().get_pointer_Bfield_fp)

    WARPX_GET_FIELD(warpx_getCurrentDensity, WarpX::GetInstance().get_pointer_current_fp)
    WARPX_GET_FIELD(warpx_getCurrentDensityCP, WarpX::GetInstance().get_pointer_current_cp)
    WARPX_GET_FIELD(warpx_getCurrentDensityFP, WarpX::GetInstance().get_pointer_current_fp)

    WARPX_GET_LOVECTS(warpx_getEfieldLoVects, WarpX::GetInstance().get_pointer_Efield_aux)
    WARPX_GET_LOVECTS(warpx_getEfieldCPLoVects, WarpX::GetInstance().get_pointer_Efield_cp)
    WARPX_GET_LOVECTS(warpx_getEfieldFPLoVects, WarpX::GetInstance().get_pointer_Efield_fp)

    WARPX_GET_LOVECTS(warpx_getBfieldLoVects, WarpX::GetInstance().get_pointer_Bfield_aux)
    WARPX_GET_LOVECTS(warpx_getBfieldCPLoVects, WarpX::GetInstance().get_pointer_Bfield_cp)
    WARPX_GET_LOVECTS(warpx_getBfieldFPLoVects, WarpX::GetInstance().get_pointer_Bfield_fp)

    WARPX_GET_LOVECTS(warpx_getCurrentDensityLoVects, WarpX::GetInstance().get_pointer_current_fp)
    WARPX_GET_LOVECTS(warpx_getCurrentDensityCPLoVects, WarpX::GetInstance().get_pointer_current_cp)
    WARPX_GET_LOVECTS(warpx_getCurrentDensityFPLoVects, WarpX::GetInstance().get_pointer_current_fp)

    int* warpx_getEx_nodal_flag() {return getFieldNodalFlagData( WarpX::GetInstance().get_pointer_Efield_aux(0,0) );}
    int* warpx_getEy_nodal_flag() {return getFieldNodalFlagData( WarpX::GetInstance().get_pointer_Efield_aux(0,1) );}
    int* warpx_getEz_nodal_flag() {return getFieldNodalFlagData( WarpX::GetInstance().get_pointer_Efield_aux(0,2) );}
    int* warpx_getBx_nodal_flag() {return getFieldNodalFlagData( WarpX::GetInstance().get_pointer_Bfield_aux(0,0) );}
    int* warpx_getBy_nodal_flag() {return getFieldNodalFlagData( WarpX::GetInstance().get_pointer_Bfield_aux(0,1) );}
    int* warpx_getBz_nodal_flag() {return getFieldNodalFlagData( WarpX::GetInstance().get_pointer_Bfield_aux(0,2) );}
    int* warpx_getJx_nodal_flag() {return getFieldNodalFlagData( WarpX::GetInstance().get_pointer_current_fp(0,0) );}
    int* warpx_getJy_nodal_flag() {return getFieldNodalFlagData( WarpX::GetInstance().get_pointer_current_fp(0,1) );}
    int* warpx_getJz_nodal_flag() {return getFieldNodalFlagData( WarpX::GetInstance().get_pointer_current_fp(0,2) );}
    int* warpx_getRho_nodal_flag() {return getFieldNodalFlagData( WarpX::GetInstance().get_pointer_rho_fp(0) );}
    int* warpx_getPhi_nodal_flag() {return getFieldNodalFlagData( WarpX::GetInstance().get_pointer_phi_fp(0) );}
    int* warpx_getF_nodal_flag() {return getFieldNodalFlagData( WarpX::GetInstance().get_pointer_F_fp(0) );}
    int* warpx_getG_nodal_flag() {return getFieldNodalFlagData( WarpX::GetInstance().get_pointer_G_fp(0) );}

#define WARPX_GET_SCALAR(SCALAR, GETTER) \
    amrex::Real** SCALAR(int lev, \
                         int *return_size, int *ncomps, int **ngrowvect, int **shapes) { \
        auto * mf = GETTER(lev); \
        if (mf != nullptr) { \
            return getMultiFabPointers(*mf, return_size, ncomps, ngrowvect, shapes); \
        } else { \
            return nullptr; \
        } \
    }

#define WARPX_GET_LOVECTS_SCALAR(SCALAR, GETTER) \
    int* SCALAR(int lev, \
                int *return_size, int **ngrowvect) { \
        auto * mf = GETTER(lev); \
        if (mf != nullptr) { \
            return getMultiFabLoVects(*mf, return_size, ngrowvect); \
        } else { \
            return nullptr; \
        } \
    }

    WARPX_GET_SCALAR(warpx_getChargeDensityCP, WarpX::GetInstance().get_pointer_rho_cp)
    WARPX_GET_SCALAR(warpx_getChargeDensityFP, WarpX::GetInstance().get_pointer_rho_fp)

    WARPX_GET_LOVECTS_SCALAR(warpx_getChargeDensityCPLoVects, WarpX::GetInstance().get_pointer_rho_cp)
    WARPX_GET_LOVECTS_SCALAR(warpx_getChargeDensityFPLoVects, WarpX::GetInstance().get_pointer_rho_fp)

    WARPX_GET_SCALAR(warpx_getPhiFP, WarpX::GetInstance().get_pointer_phi_fp)

    WARPX_GET_LOVECTS_SCALAR(warpx_getPhiFPLoVects, WarpX::GetInstance().get_pointer_phi_fp)

    // F and G
    WARPX_GET_SCALAR(warpx_getFfieldCP, WarpX::GetInstance().get_pointer_F_cp)
    WARPX_GET_SCALAR(warpx_getFfieldFP, WarpX::GetInstance().get_pointer_F_fp)
    WARPX_GET_LOVECTS_SCALAR(warpx_getFfieldCPLoVects, WarpX::GetInstance().get_pointer_F_cp)
    WARPX_GET_LOVECTS_SCALAR(warpx_getFfieldFPLoVects, WarpX::GetInstance().get_pointer_F_fp)
    WARPX_GET_SCALAR(warpx_getGfieldCP, WarpX::GetInstance().get_pointer_G_cp)
    WARPX_GET_SCALAR(warpx_getGfieldFP, WarpX::GetInstance().get_pointer_G_fp)
    WARPX_GET_LOVECTS_SCALAR(warpx_getGfieldCPLoVects, WarpX::GetInstance().get_pointer_G_cp)
    WARPX_GET_LOVECTS_SCALAR(warpx_getGfieldFPLoVects, WarpX::GetInstance().get_pointer_G_fp)

    void warpx_depositRhoSpecies ( const char* char_species_name ) {
        // Call the same function used in ElectrostaticSolver.cpp to
        // deposit charge density from a specific species on the grid
        // and write it to rho_fp. The values in rho_fp will be overwritten.
        const std::string species_name(char_species_name);

        WarpX& warpx = WarpX::GetInstance();
        const auto & mypc = warpx.GetPartContainer();
        auto & myspc = mypc.GetParticleContainerFromName(species_name);
        warpx.DepositChargeDensity(myspc, true, true);
        warpx.ChargeDensityGridProcessing();
        return;
    }

#define WARPX_GET_FIELD_PML(FIELD, GETTER) \
    amrex::Real** FIELD(int lev, int direction, \
                        int *return_size, int *ncomps, int **ngrowvect, int **shapes) { \
        auto * pml = WarpX::GetInstance().GetPML(lev); \
        if (!pml) return nullptr; \
        auto * mf = (pml->GETTER()[direction]); \
        if (!mf) return nullptr; \
        return getMultiFabPointers(*mf, return_size, ncomps, ngrowvect, shapes); \
    }

#define WARPX_GET_LOVECTS_PML(FIELD, GETTER) \
    int* FIELD(int lev, int direction, \
               int *return_size, int **ngrowvect) { \
        auto * pml = WarpX::GetInstance().GetPML(lev); \
        if (!pml) return nullptr; \
        auto * mf = (pml->GETTER()[direction]); \
        if (!mf) return nullptr; \
        return getMultiFabLoVects(*mf, return_size, ngrowvect); \
    }

    WARPX_GET_FIELD_PML(warpx_getEfieldCP_PML, GetE_cp)
    WARPX_GET_FIELD_PML(warpx_getEfieldFP_PML, GetE_fp)
    WARPX_GET_FIELD_PML(warpx_getBfieldCP_PML, GetB_cp)
    WARPX_GET_FIELD_PML(warpx_getBfieldFP_PML, GetB_fp)
    WARPX_GET_FIELD_PML(warpx_getCurrentDensityCP_PML, Getj_cp)
    WARPX_GET_FIELD_PML(warpx_getCurrentDensityFP_PML, Getj_fp)
    WARPX_GET_LOVECTS_PML(warpx_getEfieldCPLoVects_PML, GetE_cp)
    WARPX_GET_LOVECTS_PML(warpx_getEfieldFPLoVects_PML, GetE_fp)
    WARPX_GET_LOVECTS_PML(warpx_getBfieldCPLoVects_PML, GetB_cp)
    WARPX_GET_LOVECTS_PML(warpx_getBfieldFPLoVects_PML, GetB_fp)
    WARPX_GET_LOVECTS_PML(warpx_getCurrentDensityCPLoVects_PML, Getj_cp)
    WARPX_GET_LOVECTS_PML(warpx_getCurrentDensityFPLoVects_PML, Getj_fp)

    amrex::ParticleReal** warpx_getParticleStructs(
            const char* char_species_name, int lev,
            int* num_tiles, int** particles_per_tile) {
        const auto & mypc = WarpX::GetInstance().GetPartContainer();
        const std::string species_name(char_species_name);
        auto & myspc = mypc.GetParticleContainerFromName(species_name);

        int i = 0;
        for (WarpXParIter pti(myspc, lev); pti.isValid(); ++pti, ++i) {}

        // *num_tiles = myspc.numLocalTilesAtLevel(lev);
        *num_tiles = i;
        *particles_per_tile = (int*) malloc(*num_tiles*sizeof(int));

        amrex::ParticleReal** data = (amrex::ParticleReal**) malloc(*num_tiles*sizeof(typename WarpXParticleContainer::ParticleType*));
        i = 0;
        for (WarpXParIter pti(myspc, lev); pti.isValid(); ++pti, ++i) {
            auto& aos = pti.GetArrayOfStructs();
            data[i] = (amrex::ParticleReal*) aos.data();
            (*particles_per_tile)[i] = pti.numParticles();
        }
        return data;
    }

    amrex::ParticleReal** warpx_getParticleArrays (
            const char* char_species_name, const char* char_comp_name,
            int lev, int* num_tiles, int** particles_per_tile ) {

        const auto & mypc = WarpX::GetInstance().GetPartContainer();
        const std::string species_name(char_species_name);
        auto & myspc = mypc.GetParticleContainerFromName(species_name);

        int comp = warpx_getParticleCompIndex(char_species_name, char_comp_name);

        int i = 0;
        for (WarpXParIter pti(myspc, lev); pti.isValid(); ++pti, ++i) {}

        // *num_tiles = myspc.numLocalTilesAtLevel(lev);
        *num_tiles = i;
        *particles_per_tile = (int*) malloc(*num_tiles*sizeof(int));

        amrex::ParticleReal** data = (amrex::ParticleReal**) malloc(*num_tiles*sizeof(amrex::ParticleReal*));
        i = 0;
        for (WarpXParIter pti(myspc, lev); pti.isValid(); ++pti, ++i) {
            auto& soa = pti.GetStructOfArrays();
            data[i] = (amrex::ParticleReal*) soa.GetRealData(comp).dataPtr();
            (*particles_per_tile)[i] = pti.numParticles();
        }
        return data;
    }

    int warpx_getParticleCompIndex (
         const char* char_species_name, const char* char_comp_name )
    {
        const auto & mypc = WarpX::GetInstance().GetPartContainer();

        const std::string species_name(char_species_name);
        auto & myspc = mypc.GetParticleContainerFromName(species_name);

        const std::string comp_name(char_comp_name);
        auto particle_comps = myspc.getParticleComps();

        return particle_comps.at(comp_name);
    }

    void warpx_addRealComp(const char* char_species_name,
        const char* char_comp_name, bool comm=true)
    {
        auto & mypc = WarpX::GetInstance().GetPartContainer();
        const std::string species_name(char_species_name);
        auto & myspc = mypc.GetParticleContainerFromName(species_name);

        const std::string comp_name(char_comp_name);
        myspc.AddRealComp(comp_name, comm);

        mypc.defineAllParticleTiles();
    }

    int warpx_getParticleBoundaryBufferSize(const char* species_name, int boundary)
    {
        const std::string name(species_name);
        auto& particle_buffers = WarpX::GetInstance().GetParticleBoundaryBuffer();
        return particle_buffers.getNumParticlesInContainer(species_name, boundary);
    }

    int** warpx_getParticleBoundaryBufferScrapedSteps(const char* species_name, int boundary, int lev,
                     int* num_tiles, int** particles_per_tile)
    {
        const std::string name(species_name);
        auto& particle_buffers = WarpX::GetInstance().GetParticleBoundaryBuffer();
        auto& particle_buffer = particle_buffers.getParticleBuffer(species_name, boundary);

        const int comp = particle_buffer.NumIntComps() - 1;

        int i = 0;
        for (amrex::ParIter<0,0,PIdx::nattribs, 0, amrex::PinnedArenaAllocator> pti(particle_buffer, lev); pti.isValid(); ++pti, ++i) {}

        // *num_tiles = myspc.numLocalTilesAtLevel(lev);
        *num_tiles = i;
        *particles_per_tile = (int*) malloc(*num_tiles*sizeof(int));

        int** data = (int**) malloc(*num_tiles*sizeof(int*));
        i = 0;
        for (amrex::ParIter<0,0,PIdx::nattribs, 0, amrex::PinnedArenaAllocator> pti(particle_buffer, lev); pti.isValid(); ++pti, ++i) {
            auto& soa = pti.GetStructOfArrays();
            data[i] = (int*) soa.GetIntData(comp).dataPtr();
            (*particles_per_tile)[i] = pti.numParticles();
        }

        return data;
    }

    amrex::ParticleReal** warpx_getParticleBoundaryBuffer(const char* species_name, int boundary, int lev,
                     int* num_tiles, int** particles_per_tile, const char* comp_name)
    {
        const std::string name(species_name);
        auto& particle_buffers = WarpX::GetInstance().GetParticleBoundaryBuffer();
        auto& particle_buffer = particle_buffers.getParticleBuffer(species_name, boundary);

        const int comp = warpx_getParticleCompIndex(species_name, comp_name);

        int i = 0;
        for (amrex::ParIter<0,0,PIdx::nattribs, 0, amrex::PinnedArenaAllocator> pti(particle_buffer, lev); pti.isValid(); ++pti, ++i) {}

        // *num_tiles = myspc.numLocalTilesAtLevel(lev);
        *num_tiles = i;
        *particles_per_tile = (int*) malloc(*num_tiles*sizeof(int));

        amrex::ParticleReal** data = (amrex::ParticleReal**) malloc(*num_tiles*sizeof(amrex::ParticleReal*));
        i = 0;
        for (amrex::ParIter<0,0,PIdx::nattribs, 0, amrex::PinnedArenaAllocator> pti(particle_buffer, lev); pti.isValid(); ++pti, ++i) {
            auto& soa = pti.GetStructOfArrays();
            data[i] = (amrex::ParticleReal*) soa.GetRealData(comp).dataPtr();
            (*particles_per_tile)[i] = pti.numParticles();
        }

        return data;
    }

    void warpx_clearParticleBoundaryBuffer () {
        auto& particle_buffers = WarpX::GetInstance().GetParticleBoundaryBuffer();
        particle_buffers.clearParticles();
    }

    void warpx_ComputeDt () {
        WarpX& warpx = WarpX::GetInstance();
        warpx.ComputeDt ();
    }
    void warpx_MoveWindow (int step,bool move_j) {
        WarpX& warpx = WarpX::GetInstance();
        warpx.MoveWindow (step, move_j);
    }

    void warpx_EvolveE (amrex::Real dt) {
        WarpX& warpx = WarpX::GetInstance();
        warpx.EvolveE (dt);
    }
    void warpx_EvolveB (amrex::Real dt, DtType a_dt_type) {
        WarpX& warpx = WarpX::GetInstance();
        warpx.EvolveB (dt, a_dt_type);
    }
    void warpx_FillBoundaryE () {
        WarpX& warpx = WarpX::GetInstance();
        warpx.FillBoundaryE (warpx.getngE());
    }
    void warpx_FillBoundaryB () {
        WarpX& warpx = WarpX::GetInstance();
        warpx.FillBoundaryB (warpx.getngE());
    }
    void warpx_SyncCurrent () {
        WarpX& warpx = WarpX::GetInstance();
        warpx.SyncCurrent ();
    }
    void warpx_UpdateAuxilaryData () {
        WarpX& warpx = WarpX::GetInstance();
        warpx.UpdateAuxilaryData ();
    }
    void warpx_PushParticlesandDepose (amrex::Real cur_time) {
        WarpX& warpx = WarpX::GetInstance();
        warpx.PushParticlesandDepose (cur_time);
    }

    int warpx_getistep (int lev) {
        WarpX& warpx = WarpX::GetInstance();
        return warpx.getistep (lev);
    }
    void warpx_setistep (int lev, int ii) {
        WarpX& warpx = WarpX::GetInstance();
        warpx.setistep (lev, ii);
    }
    amrex::Real warpx_gett_new (int lev) {
        WarpX& warpx = WarpX::GetInstance();
        return warpx.gett_new (lev);
    }
    void warpx_sett_new (int lev, amrex::Real time) {
        WarpX& warpx = WarpX::GetInstance();
        warpx.sett_new (lev, time);
    }
    amrex::Real warpx_getdt (int lev) {
        WarpX& warpx = WarpX::GetInstance();
        return warpx.getdt (lev);
    }

    int warpx_maxStep () {
        WarpX& warpx = WarpX::GetInstance();
        return warpx.maxStep ();
    }
    amrex::Real warpx_stopTime () {
        WarpX& warpx = WarpX::GetInstance();
        return warpx.stopTime ();
    }

    int warpx_finestLevel () {
        WarpX& warpx = WarpX::GetInstance();
        return warpx.finestLevel ();
    }

    int warpx_getMyProc () {
        return amrex::ParallelDescriptor::MyProc();
    }

    int warpx_getNProcs () {
        return amrex::ParallelDescriptor::NProcs();
    }

    void mypc_Redistribute () {
        auto & mypc = WarpX::GetInstance().GetPartContainer();
        mypc.Redistribute();
    }

    amrex::Real eval_expression_t ( const char* char_expr, const amrex::Real t) {
        const std::string expr(char_expr);

        auto parser = makeParser(expr, {"t"});
        auto parser_exe = parser.compileHost<1>();
        return parser_exe(t);
    }

    void warpx_moveParticlesBetweenSpecies(const char* char_src_species_name,
        const char* char_dst_species_name, const int lev)
    {
        auto & mypc = WarpX::GetInstance().GetPartContainer();
        const std::string src_species_name(char_src_species_name);
        auto & src_spc = mypc.GetParticleContainerFromName(src_species_name);
        const std::string dst_species_name(char_dst_species_name);
        auto & dst_spc = mypc.GetParticleContainerFromName(dst_species_name);

        for (WarpXParIter pti(src_spc, lev); pti.isValid(); ++pti) {
            auto& src_tile = src_spc.ParticlesAt(lev, pti);
            auto& dst_tile = dst_spc.ParticlesAt(lev, pti);

            auto src_np = src_tile.numParticles();
            auto dst_np = dst_tile.numParticles();

            dst_tile.resize(dst_np + src_np);
            amrex::copyParticles(dst_tile, src_tile, 0, dst_np, src_np);
        }

        // clear the source species
        src_spc.clearParticles();
    }

    void warpx_calcSchottkyWeight(const char* char_species_name,
        const amrex::ParticleReal pre_fac, const int lev)
    {
        // get the particle container for the species of interest
        auto & mypc = WarpX::GetInstance().GetPartContainer();
        const std::string species_name(char_species_name);
        auto & myspc = mypc.GetParticleContainerFromName(species_name);
        const auto & particle_comps = myspc.getParticleComps();

        const auto & plo = myspc.Geom(lev).ProbLoArray();
        const auto & dxi = myspc.Geom(lev).InvCellSizeArray();

        // get the electric field components
        auto& Ex = WarpX::GetInstance().getEfield(lev, 0);
        auto& Ey = WarpX::GetInstance().getEfield(lev, 1);
        auto& Ez = WarpX::GetInstance().getEfield(lev, 2);

        for (WarpXParIter pti(myspc, lev); pti.isValid(); ++pti) {

            // get the data on the grid
            const auto Ex_arr = Ex[pti].array();
            const auto Ey_arr = Ey[pti].array();
            const auto Ez_arr = Ez[pti].array();

            // get the particle data
            const long np = pti.numParticles();
            const auto getPosition = GetParticlePosition(pti);

            auto& attribs = pti.GetAttribs();
            amrex::ParticleReal* w = attribs[PIdx::w].dataPtr();
            amrex::ParticleReal* norm_x = pti.GetAttribs(particle_comps.at("norm_x")).dataPtr();
            amrex::ParticleReal* norm_y = pti.GetAttribs(particle_comps.at("norm_y")).dataPtr();
            amrex::ParticleReal* norm_z = pti.GetAttribs(particle_comps.at("norm_z")).dataPtr();

            // TODO change this function to instead take as argument the
            // boundary from which injection is done and get the normal
            // from `interp_normal` (in DistanceToEB.H) or as a hard coded
            // vector for domain boundaries

            amrex::ParallelFor( np, [=] AMREX_GPU_DEVICE (long ip)
            {
                // get the particle position
                amrex::ParticleReal xp, yp, zp;
                getPosition(ip, xp, yp, zp);

                // get the weight of each neigbouring node to use
                // during interpolation
                int i, j, k;
                amrex::Real W[AMREX_SPACEDIM][2];
                compute_weights_nodal(xp, yp, zp, plo, dxi, i, j, k, W);

                // interpolate the electric field to the particle position
                amrex::Real Ex_p = interp_field_nodal(i, j, k, W, Ex_arr);
                amrex::Real Ey_p = interp_field_nodal(i, j, k, W, Ey_arr);
                amrex::Real Ez_p = interp_field_nodal(i, j, k, W, Ez_arr);

                // calculate the dot product of the electric field with the
                // normal vector tied to the particle
                amrex::Real normal_field = (
                    Ex_p * norm_x[ip] + Ey_p * norm_y[ip] + Ez_p * norm_z[ip]
                );

                // increase the particle weight by the Schottky enhancement
                // factor if needed
                w[ip] *= ((normal_field < 0.0) ?
                    std::exp(pre_fac * std::sqrt(-normal_field)) : 1.0
                );
            });
        }
    }
}
