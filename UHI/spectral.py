"""Planck-weighted, diffuse longwave bands. Wavelengths in micrometres.
Atmospheric transmittance is hemispherical effective transmittance, not zenith.
"""
from functools import lru_cache
import copy
import numpy as np
SIGMA=5.670374419e-8
C2=14387.768775039337
GRID=np.arange(173.15,453.15001,.05)

def _tail(x):
    raw=np.asarray(x,float);x=np.clip(raw,.05,745.);out=np.zeros_like(x)
    for n in range(1,161):
        out+=np.exp(-n*x)*(x**3/n+3*x*x/n**2+6*x/n**3+6/n**4)
    series=1-15/np.pi**4*(raw**3/3-raw**4/8+raw**5/60-raw**7/5040+raw**9/272160) if np.all(raw<.05) else None
    if series is not None:return series
    small=np.minimum(raw,.05)
    lower=1-15/np.pi**4*(small**3/3-small**4/8+small**5/60-small**7/5040+small**9/272160)
    return np.where(raw<.05,lower,np.where(raw>745,0.,out*15/np.pi**4))

def exact_band_exitance(t,lower,upper):
    """Blackbody hemispherical power; full [0,infinity) is exactly sigma T^4."""
    t=np.asarray(t,float)
    hi=np.ones_like(t) if upper is None else _tail(C2/(upper*t))
    lo=np.zeros_like(t) if lower==0 else _tail(C2/(lower*t))
    return SIGMA*t**4*(hi-lo)

@lru_cache(maxsize=128)
def _table(lower,upper):return exact_band_exitance(GRID,lower,upper)

def blackbody(t,bands):
    t=np.asarray(t,float)
    if np.any(~np.isfinite(t)) or np.any(t<=0):raise ValueError('Temperature must be finite kelvin')
    if np.all((t>=GRID[0])&(t<=GRID[-1])):
        return np.stack([np.interp(t,GRID,_table(b['lower_um'],b['upper_um'])) for b in bands],axis=-1)
    return np.stack([exact_band_exitance(t,b['lower_um'],b['upper_um']) for b in bands],axis=-1)

def validate_config(config):
    c=copy.deepcopy(config)
    allowed={'enabled','bands','materials','building_materials','sky_mode','reference_temperature_k','reflection_mode'}
    if set(c)-allowed:raise ValueError('Unknown longwave options: '+str(set(c)-allowed))
    if not c.get('enabled',False):return None
    bands=c.get('bands')
    if not bands or len(bands)>128:raise ValueError('longwave.bands requires 1–128 bands')
    prev=0.;names=[]
    for i,b in enumerate(bands):
        if set(b)-{'name','lower_um','upper_um','atmospheric_transmittance'}:raise ValueError('Unknown band field')
        lo=float(b['lower_um']);hi=b['upper_um'];hi=None if hi is None else float(hi)
        tau=float(b['atmospheric_transmittance'])
        if not np.isfinite(lo) or lo!=prev or (hi is None and i!=len(bands)-1) or (hi is not None and (not np.isfinite(hi) or hi<=lo)):
            raise ValueError('Bands must partition [0,infinity), ordered without gaps/overlaps')
        if not np.isfinite(tau) or not 0<=tau<=1:raise ValueError('Atmospheric transmittance must be in [0,1]')
        b.update(lower_um=lo,upper_um=hi,atmospheric_transmittance=tau);prev=hi
        names.append(b['name'])
    if prev is not None or len(set(names))!=len(names):raise ValueError('Unique band names and an infinite final band required')
    if c.get('sky_mode','prescribed') not in ('prescribed','epw_matched'):raise ValueError('Unknown spectral sky mode')
    if c.get('reflection_mode','one_bounce') not in ('one_bounce','radiosity'):raise ValueError('Unknown longwave reflection mode')
    c.setdefault('reference_temperature_k',300.)
    if not np.isfinite(c['reference_temperature_k']) or c['reference_temperature_k']<=0:raise ValueError('Invalid reference temperature')
    allowed_surfaces={'wall','roof','window','skylight','ground','land','water'}
    if set(c.get('materials',{}))-allowed_surfaces:raise ValueError('Unknown longwave surface material')
    for mapping in [c.get('materials',{}),*c.get('building_materials',{}).values()]:
        if set(mapping)-allowed_surfaces:raise ValueError('Unknown building surface')
        for spec in mapping.values():resolve_material(spec,.9,bands,c['reference_temperature_k'])
    return c

def resolve_material(spec,default_eps,bands,reference=300.):
    """Kirchhoff alpha=epsilon. Opaque surfaces require epsilon+rho=1.
Missing rho: mean of explicitly supplied other bands; if none use 1-epsilon.
A nonopaque material may declare tau; transmission exits this exterior model.
"""
    spec=spec or {};names=[b['name'] for b in bands]
    if set(spec)-{'emissivity','reflectance','opaque'}:raise ValueError('Unknown spectral material field')
    es=spec.get('emissivity',{});rs=spec.get('reflectance',{})
    if not isinstance(es,dict) or not isinstance(rs,dict) or (set(es)|set(rs))-set(names):raise ValueError('Unknown material band')
    known=[float(x) for x in rs.values() if x is not None]
    eps=np.array([es.get(k,default_eps) for k in names],float)
    rho=np.array([rs[k] if rs.get(k) is not None else (np.mean(known) if known else 1-e) for k,e in zip(names,eps)],float)
    if np.any(~np.isfinite(eps)) or np.any(~np.isfinite(rho)) or np.any(eps<0) or np.any(eps>1) or np.any(rho<0) or np.any(rho>1):raise ValueError('Spectral material coefficients must be in [0,1]')
    if np.any(eps+rho>1+1e-10):raise ValueError('Longwave emissivity + reflectance exceeds 1 after fallback')
    if spec.get('opaque',True) and np.any(abs(eps+rho-1)>1e-10):raise ValueError('Opaque longwave material must have epsilon + rho = 1; check mean-reflectance fallback')
    fraction=blackbody(reference,bands);eff=float(eps@fraction/(SIGMA*reference**4))
    if eff>=1-1e-12:raise ValueError('Planck-weighted integrated emissivity must be < 1')
    return eps,rho,np.maximum(0,1-eps-rho)

def surface_spec(config,name,index=None):
    base=copy.deepcopy(config.get('materials',{}).get(name,{}))
    override=config.get('building_materials',{}).get(str(index),{}).get(name,{})
    for k,v in override.items():
        if isinstance(v,dict):base.setdefault(k,{}).update(v)
        else:base[k]=v
    return base

def atmosphere(config,ambient_c,sky_ir):
    b=blackbody(ambient_c+273.15,config['bands']);tau=np.array([x['atmospheric_transmittance'] for x in config['bands']])
    scale=1.
    if config.get('sky_mode','prescribed')=='epw_matched':
        target=float(sky_ir)
        if target<0 or target>float(b.sum())+1e-7:raise ValueError('EPW-matched sky must lie between zero and ambient blackbody exitance; choose prescribed for inversion skies')
        # Bounded optical-depth scaling: endpoints must permit the requested total.
        def sky(s):return (1-np.power(tau,s))*b
        if target<sky(1e-10).sum()-1e-6 or target>sky(1e6).sum()+1e-6:raise ValueError('Atmospheric profile cannot match EPW IR; avoid exact 0/1 transmittances')
        lo,hi=1e-10,1e6
        for _ in range(60):
            mid=(lo+hi)/2
            if sky(mid).sum()<target:lo=mid
            else:hi=mid
        scale=(lo+hi)/2;tau=np.power(tau,scale)
    return (1-tau)*b,tau,scale

def attach_to_payload(payload,config):
    """Standalone exterior boundary without an intersurface geometry network."""
    c=validate_config(config)
    if c is None:return
    for s in payload['surfaces']:
        if s['kind']=='floor':continue
        eps,_,_=resolve_material(surface_spec(c,s['name']),s['emissivity'],c['bands'],c['reference_temperature_k'])
        if s['emissivity']<=0:raise ValueError('Spectral adapter requires positive carrier emissivity')
        s['longwave_bands']=[dict(lower_um=b['lower_um'],upper_um=b['upper_um'],emissivity=float(e)) for b,e in zip(c['bands'],eps)]
    for h in payload['hours']:
        sky,_,_=atmosphere(c,h['air_c'],h['sky_ir_w_m2']);ground=blackbody(h['ground_c']+273.15,c['bands'])
        h['network_ir_w_m2']=[0. if s['kind']=='floor' else float(np.array([b['emissivity'] for b in s['longwave_bands']])@(s['sky_view']*sky+s['ground_view']*ground)/s['emissivity']) for s in payload['surfaces']]
