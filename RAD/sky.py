"""Explicit portable sky approximation; not Perez/gendaymtx."""
import numpy as np
from WEA.epw import load_epw_rows,epw2location,load_sun_vectors,fix_radiation
def build_portable_sky(epw,output):
    """Isotropic diffuse + nearest-patch solar beam; hourly GHI normalized.

    NOAA approximate declination/equation-of-time, midpoint of EPW interval.
    This is a documented test approximation, not the Radiance Perez sky model.
    """
    _,rows,_=load_epw_rows(epw)
    data=np.asarray(rows,dtype=object)
    loc=epw2location(epw)
    doy=np.arange(len(rows))//24+1
    hour=np.arange(len(rows))%24+.5
    gamma=2*np.pi/365*(doy-1+(hour-12)/24)
    decl=(.006918-.399912*np.cos(gamma)+.070257*np.sin(gamma)-.006758*np.cos(2*gamma)
          +.000907*np.sin(2*gamma)-.002697*np.cos(3*gamma)+.00148*np.sin(3*gamma))
    eot=229.18*(.000075+.001868*np.cos(gamma)-.032077*np.sin(gamma)-.014615*np.cos(2*gamma)-.040849*np.sin(2*gamma))
    timezone=float(epw.read_text(errors='replace').splitlines()[0].split(',')[8])
    angle=np.deg2rad((hour*60+eot+4*loc.longitude-60*timezone)/4-180)
    lat=np.deg2rad(loc.latitude)
    sun=np.column_stack((-np.cos(decl)*np.sin(angle),
        np.cos(lat)*np.sin(decl)-np.sin(lat)*np.cos(decl)*np.cos(angle),
        np.sin(lat)*np.sin(decl)+np.cos(lat)*np.cos(decl)*np.cos(angle)))
    rays=load_sun_vectors();rays=rays/np.linalg.norm(rays,axis=1)[:,None]
    dhi=data[:,15].astype(float);dni=data[:,14].astype(float);ghi=data[:,13].astype(float)
    # Each patch's solid angle follows Tregenza row geometry, in the existing order.
    from WEA.epw import TREGENZA_COEFFICIENTS,TREGENZA_PATCHES_PER_ROW
    omega=np.repeat(TREGENZA_COEFFICIENTS,TREGENZA_PATCHES_PER_ROW)
    diffuse=np.maximum(dhi,0)[None,:]*omega[:,None]/np.pi
    matrix=diffuse.copy()
    nearest=np.argmax(rays@sun.T,axis=0)
    matrix[nearest,np.arange(len(rows))]+=np.where(sun[:,2]>0,np.maximum(dni,0),0)
    matrix=fix_radiation(matrix,np.maximum(ghi,0))
    np.save(output,matrix)
