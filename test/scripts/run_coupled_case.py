"""Reproduce a coupled SHP/EPW test, with explicit portable sky approximation.

python test/scripts/run_coupled_case.py --input-zip case.zip --output outputs/coupled_rebuild
Default: hottest EPW day and next two days, 10% exact ray sample, seed=42.
"""
import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from test.paths import ENERGY_CONFIGS, OUTPUT
from ENERGY import Neighborhood
from RAD.workflow import compute_building_radiation
from WEA.epw import load_epw_rows,epw2location,load_sun_vectors,fix_radiation


def portable_sky(epw,output):
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


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--input-zip',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=OUTPUT/'coupled_rebuild')
    parser.add_argument('--climate-zone',type=int,default=4);parser.add_argument('--days',type=int,default=3);parser.add_argument('--sample',type=float,default=.1)
    parser.add_argument('--max-preheat',type=int,default=120);parser.add_argument('--preheat',type=int,default=4)
    parser.add_argument('--resume-warmup',action='store_true');parser.add_argument('--radiation-cache',type=Path);parser.add_argument('--start-hoy',type=int)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    inputs=args.output/'input';inputs.mkdir(exist_ok=True)
    with zipfile.ZipFile(args.input_zip) as archive:
        for name in archive.namelist():
            target=(inputs/name).resolve()
            if not target.is_relative_to(inputs.resolve()):raise ValueError('Unsafe archive path')
        archive.extractall(inputs)
    shp=next(inputs.rglob('*.shp'));epw=next(inputs.rglob('*.epw'))
    _,rows,_=load_epw_rows(epw);temperature=np.array([float(r[6]) for r in rows])
    start=int(np.argmax(temperature)//24*24) if args.start_hoy is None else args.start_hoy
    if start+24*args.days>8760:raise ValueError('Requested interval exceeds EPW')
    n=Neighborhood();n.readSHP(shp,fvc=.2,fwc=.05)
    n.readEPW(epw,start_hoy=start,end_hoy=start+24*args.days,smooth_weather=False,climate_zone=args.climate_zone)
    print(f'Inventory={len(n.buildings)}, HOY=[{start},{start+24*args.days})',flush=True)
    if args.radiation_cache is None:
        sky=args.output/'portable_sky.npy';portable_sky(epw,sky)
        radiation=compute_building_radiation(shp,sky,sample=args.sample,seed=42)
    else:radiation=args.radiation_cache
    n._rad=str(radiation.resolve());n.sampleInstance(seed=42)
    import pandas as pd
    pd.DataFrame([dict(building_index=i,archetype=b.archetype,wall_u=b.WallUValue,
        window_u=b.WindowUValue,window_shgc=b.WindowSHGC,wwr=b.wwr,gfa_m2=b.GFA)
        for i,b in enumerate(n.buildings)]).to_csv(args.output/'archetype_parameters.csv',index=False)
    result=n.uhiSimulation(energy_model='v7',preheat=args.preheat,
        energy_kwargs={'output_dir':args.output,'max_preheat':args.max_preheat,'warmup_checkpoint':args.output/'warmup_checkpoint.json' if args.resume_warmup else None})
    import geopandas as gpd
    gdf=gpd.read_file(shp).to_crs(4326)
    centroid=gdf.geometry.union_all().centroid
    from ENERGY.result_store import read_results
    external=read_results(result['building_result_file'])
    summary=dict(buildings=len(n.buildings),hours=len(result['hourly']),start_hoy=start,
        max_balance_residual_w_m2=max(r['max_residual_w_m2'] for r in result['hourly']),
        mean_warming_k=float(np.mean(result['Tair']-result['Tambient'])),
        max_building_balance_w=max(abs(r['balance_residual_w']) for b in external['buildings'] for r in b['hours']),
        wall_u_min=min(b.WallUValue for b in n.buildings),wall_u_below_01=sum(b.WallUValue<.1 for b in n.buildings),
        max_shortwave_partition_residual_w=max(abs(v['incident_w']-v['reflected_w']-v['absorbed_w']-v['transmitted_w'])
            for b in external['buildings'] for r in b['hours'] for v in r['surfaces']),
        max_storage_power_conversion_residual_w=max(abs(v['storage_delta_kwh']*1000-v['storage_w'])
            for b in external['buildings'] for r in b['hours'] for v in r['surfaces']),
        max_window_storage_w=max(abs(s['storage_w']) for b in external['buildings'] for r in b['hours'] for s in r['surfaces'] if s['name'] in {'window','skylight'}),
        warmup=result['coupled']['warmup'],input_sha256=hashlib.sha256(args.input_zip.read_bytes()).hexdigest(),
        shapefile_lon_lat=[centroid.x,centroid.y],epw_location=epw2location(epw).__dict__,
        sky='Isotropic diffuse + nearest Tregenza beam, GHI normalized; not Perez.',
        radiation_sample=args.sample,seed=42,green_roofs='No moisture/evaporation in coupled roof model.',
        reference='Reconstruction; previous v7 output not available for bitwise comparison.')
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2),flush=True)

if __name__=='__main__':main()
