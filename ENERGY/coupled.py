"""Hourly conservative coupling of Energy v7 with CETUS UHI ground/canopy physics.

All root evaluations use the same previous physical hour. Only a converged
root can advance state. Exposed fractions deliberately sum to <1 when roofs
occupy land: roofs have no duplicate UHI radiation, storage or convection.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.optimize import root

from UHI.aT import airDensity, combinedConvectionCoefficient, referenceVelocity
from UHI.shortwave import solarRadiationTime
from UHI.vapor import vaporHeatFlowIntensity
from .surface import SurfaceEngine, build_surface_input, SIGMA
from .surface_binary import BinarySurfaceEngine

NAMES = ('ground','land','water')


def _fractions(site):
    areas=np.array([site.AreaGround+site.AreaRoad,site.AreaLandscape,site.AreaWater],dtype=float)
    if not np.isfinite(areas).all() or np.any(areas<0) or site.TotalArea<=0:
        raise ValueError('Invalid exposed areas')
    total=float(areas.sum()+site.AreaBuilding)
    if not np.isclose(total,site.TotalArea,rtol=1e-5,atol=1):
        raise ValueError('Site areas must sum to TotalArea before roof migration')
    if areas.sum()<=0:
        raise ValueError('Coupled ground receiver requires exposed ground')
    return areas/float(site.TotalArea)


def _radiation(neighborhood, climates):
    if neighborhood._rad is None:
        return None
    from RAD.workflow import prepare_buildings,read_radiation_intensities
    gdf=prepare_buildings(Path(neighborhood._shp),'index',neighborhood._height_field)
    values=read_radiation_intensities(neighborhood._rad,gdf,height_field=neighborhood._height_field)
    if len(gdf)!=len(neighborhood.buildings):
        raise ValueError('RAD inventory differs from Energy inventory')
    return values


def _shortwave(c,h,site,setting):
    incident=solarRadiationTime(float(c.RadTotal[h]),float(c.RadDiffuse[h]),site.SVF,
        site.SDL,site.SDG,site.SRTL,site.SRTG,site.fPSA,site.CL,site.CG)
    ground=site.AreaGround+site.AreaRoad
    alpha=(setting.AbsoGround*site.AreaGround+setting.AbsoRoad*site.AreaRoad)/ground if ground else 0
    # Raw incident solar: ground heat capacity supplies the temporal response.
    return incident*np.array([alpha,setting.AbsoLandscape,setting.AbsoWater])


def simulate_coupled(neighborhood, *, output_dir=None, options=None, initial_state=None,
                     preheat=4, max_preheat=120, warmup_tolerance_k=.02,
                     balance_tolerance_w_m2=.02, max_evaluations=120,
                     executable_path=None, show_progress=True, warmup_checkpoint=None,
                     warmup_acceleration=True, spinup_days=0, longwave_relaxation=1.0, engine_protocol='binary'):
    """Return legacy-shaped hourly results plus explicit fluxes and restart state.

    preheat is a minimum count, max_preheat a hard limit. On a restart preheat
    must be zero. One repeatable first-day cycle is used only at sequence start.
    Multi-day gaps are rejected. Failures leave the caller's climates unchanged.
    """
    if engine_protocol not in ('binary','json'):raise ValueError('engine_protocol must be binary or json')
    binary_engine=engine_protocol=='binary'
    if not neighborhood.buildings or not neighborhood.climate:
        raise ValueError('Initialize buildings and weather first')
    if balance_tolerance_w_m2<=0 or warmup_tolerance_k<=0 or max_evaluations<1:
        raise ValueError('Invalid convergence parameters')
    preheat=int(preheat);max_preheat=int(max_preheat);spinup_days=int(spinup_days)
    if not 0<=spinup_days<len(neighborhood.climate):raise ValueError('spinup_days must leave at least one reporting day')
    if not 0<longwave_relaxation<=1:raise ValueError('Invalid longwave_relaxation')
    if preheat<0 or max_preheat<preheat:
        raise ValueError('Invalid warmup limits')
    climates=copy.deepcopy(neighborhood.climate)
    site,setting=neighborhood.site,neighborhood.setting
    if setting.dt is None or float(setting.dt)!=3600:
        raise ValueError('Hourly coupling requires setting.dt=3600 seconds')
    fractions=_fractions(site);exposed=float(fractions.sum());area=float(site.TotalArea)
    weights=fractions/exposed
    hoy=np.concatenate([np.asarray(c.HOY,dtype=int) for c in climates])
    if np.any(np.diff(hoy)!=1):
        raise ValueError('Coupled weather must be consecutive; carry state across contiguous days')
    net=getattr(neighborhood,'_radiation_network',None)
    if net is None and neighborhood._rad and Path(neighborhood._rad).is_file() and Path(neighborhood._rad).suffix=='.npz':
        with np.load(neighborhood._rad,allow_pickle=False) as cache:
            if 'protocol' in cache and str(cache['protocol'])=='cetus-rad-network-8':
                neighborhood.radSimulation(radFile=neighborhood._rad)
                net=neighborhood._radiation_network
    if net is not None:
        if options is None:options=copy.deepcopy(net.meta.get('energy_options',{}))
        net.assert_current(neighborhood,options)
        solar=net.solar(hoy)
        rad=None
    else:
        rad=_radiation(neighborhood,climates)
    templates=[];audits=[]
    for day_index,c in enumerate(climates):
        daily=[]
        for b,building in enumerate(neighborhood.buildings):
            irr=None if rad is None else {'facade':sum(rad[k][b,c.HOY] for k in ('N','W','S','E'))/4,
                                        'roof':rad['T'][b,c.HOY]}
            if net is not None:
                irr={'facade':solar[2*b,day_index*24:(day_index+1)*24],
                     'roof':solar[2*b+1,day_index*24:(day_index+1)*24]}
            payload,audit=build_surface_input(building,c,irradiance=irr,options=options)
            daily.append(payload)
            if len(templates)==0:audits.append(audit)
        templates.append(daily)
    signature_data={'buildings':[{k:v for k,v in p.items() if k not in {'hours','initial_c'}} for p in templates[0]],
                    'site':site.to_dict(),'setting':setting.to_dict(),'radiation':net.meta['optical_signature'] if net is not None else None}
    signature=hashlib.sha256(json.dumps(signature_data,sort_keys=True,allow_nan=False).encode()).hexdigest()
    if initial_state is not None:
        if preheat:
            raise ValueError('Restart requires preheat=0')
        state=copy.deepcopy(initial_state)
        if state.get('signature')!=signature or state.get('last_hoy')!=int(hoy[0])-1:
            raise ValueError('Restart configuration or HOY differs')
        if len(state['buildings'])!=len(neighborhood.buildings):
            raise ValueError('Restart inventory differs')
    else:
        state={'air_c':float(climates[0].Tb[0]),'ground_c':[float(climates[0].Tb[0])]*3,
               'buildings':[None]*len(neighborhood.buildings),'last_hoy':int(hoy[0])-1,
               'last_shortwave':[0.0]*3,'signature':signature}
    capacities=np.array([setting.CGround,setting.CLand,setting.CWater],float)
    deep=np.array([setting.UDeepGround,setting.UDeepLand,setting.UDeepWater],float)
    ground_area=site.AreaGround+site.AreaRoad
    eps=np.array([(setting.EmiGround*site.AreaGround+setting.EmiRoad*site.AreaRoad)/ground_area if ground_area else 0,
                  setting.EmiLandscape,setting.EmiWater],float)
    if net is not None:eps[:]=net.meta['ground_emissivity']
    vapor_site=copy.deepcopy(site)
    vapor_site.greenRoofPercentage=0.0  # roofs migrated; no duplicate green-roof source
    all_rows=[];building_rows=[[] for _ in neighborhood.buildings]
    warmup_history=[];warmup_vectors=[]
    if warmup_checkpoint is not None:
        if initial_state is not None or not preheat:
            raise ValueError('Warmup checkpoint requires preheat and no production restart')
        saved=json.loads(Path(warmup_checkpoint).read_text())
        candidate=saved['state']
        if candidate.get('signature')!=signature or candidate.get('last_hoy')!=int(hoy[0])+23:
            raise ValueError('Warmup checkpoint configuration or forcing day differs')
        state=copy.deepcopy(candidate);warmup_history=copy.deepcopy(saved['history'])
    with (BinarySurfaceEngine(executable_path) if binary_engine else SurfaceEngine(executable_path)) as engine:
        engine.configure(templates[0])
        def step(c,h,models,old):
            # Preserve previous-hour UHI state even at midnight and during root trials.
            c.Ta[h-1]=old['air_c']
            for j,name in enumerate(NAMES):
                c.Tg_split[name][h-1]=old['ground_c'][j]
                c.QS[name][h-1]=old['last_shortwave'][j]
            short=np.full(3,(1-net.meta['ground_reflectance'])*solar[-1,int(np.flatnonzero(hoy==int(c.HOY[h]))[0])]) if net is not None else _shortwave(c,h,site,setting)
            for j,name in enumerate(NAMES):c.QS[name][h]=short[j]
            base=[{**p,'hours':[copy.deepcopy(p['hours'][h])]} for p in models]
            for b,p in enumerate(base):
                if old['buildings'][b] is not None:p['initial_state']=old['buildings'][b]
            ref=referenceVelocity(float(c.Velocity[h]),site.kS,setting.a,setting.k)
            for p in base:
                for j,surface in enumerate(p['surfaces']):
                    prescribed=(options or {}).get('surfaces',{}).get(surface['name'],{}).get('ho_w_m2k')
                    if prescribed is None and surface['kind']!='floor':
                        p['hours'][0]['ho_w_m2k'][j]=5.8+4.1*ref
            pressure=float(c.Pressure[h]);tb=float(c.Tb[h]);skyir=base[0]['hours'][0]['sky_ir_w_m2']
            traffic=(setting.annualDistance*setting.FE*setting.fuelDensity*setting.NHC/365/24/3600
                     *site.AreaRoad*setting.intensity/area)
            radiation_temperatures=old.get('radiation_surface_c')
            if net is not None and radiation_temperatures is None:
                radiation_temperatures=[{s['name']:old['air_c'] for s in p['surfaces']} for p in base]
            if binary_engine:engine.begin_step(base)
            radiation_iterations=0
            fixed_emission=None
            def evaluate(x, trial=False, full=False):
                ta=float(x[0]);tg=np.asarray(x[1:],float)
                if not np.isfinite(x).all() or min(x)<=-100 or max(x)>=180:
                    raise RuntimeError('Coupling root left physical temperature bracket')
                ground=float(weights@tg)
                for p in base:
                    p['hours'][0]['air_c']=ta;p['hours'][0]['ground_c']=ground
                if net is not None:
                    ir,ground_ir,emission,incoming=net.longwave_boundary(base,radiation_temperatures,list(zip(weights,tg)),skyir,ta,building_emission=fixed_emission)
                    for p,boundary in zip(base,ir):p['hours'][0]['network_ir_w_m2']=boundary
                temperatures=None
                if binary_engine:
                    level='totals' if trial else ('full' if full else 'coupling')
                    result=engine.evaluate([p['hours'][0] for p in base],response_level=level,
                        return_state=full,dynamic_fields=('air_c','ground_c','network_ir_w_m2') if net is not None else ('air_c','ground_c'))
                    building=result.get('batch')
                    temperatures=result.get('surface_temperatures')
                    air=result['air_w']/area;paired=result['ground_w']/area
                elif trial:
                    totals=engine.trial(base)
                    building=None
                    air=totals['air_w']/area;paired=totals['ground_w']/area
                else:
                    building=engine.request(base,use_models=True)
                    outs=[b['hours'][0] for b in building]
                    air=sum(o['convection_to_air_w']+o['ventilation_to_air_w']+o['hvac_to_air_w'] for o in outs)/area
                    paired=sum(o['longwave_to_ground_w']+o['contact_to_ground_w'] for o in outs)/area
                # All exposed surface groups receive an equal W/m2 flux; their area-weighted sum is paired.
                ground_input=paired/exposed
                hc=np.array([combinedConvectionCoefficient(float(t),ta,pressure,ref,setting.c1,setting.zRef,setting.z0) for t in tg])
                sensible=hc*(tg-ta)
                lw=eps*(ground_ir-SIGMA*(tg+273.15)**4) if net is not None else eps*site.SVF*(skyir-SIGMA*(tg+273.15)**4)
                c.Ta[h]=ta
                vapor=float(vaporHeatFlowIntensity(h,c,vapor_site,setting))
                canopy=combinedConvectionCoefficient(tb,ta,pressure,float(c.Velocity[h]),setting.c1,setting.zRefd,setting.z0d)*(ta-tb)
                air_storage=airDensity(pressure,ta)*1006*setting.HAir*(ta-old['air_c'])/3600
                ground_storage=capacities*(tg-np.asarray(old['ground_c']))/3600
                conduction=deep*(tg-setting.GroundTemperature)
                residual_ground=short+lw+ground_input-sensible-conduction-ground_storage
                residual_ground=np.where(fractions>0,residual_ground,tg-np.asarray(old['ground_c']))
                residual_air=float(fractions@sensible)+air+traffic+vapor-canopy-air_storage
                residual=np.r_[residual_air,residual_ground]
                return residual,dict(building=building,surface_temperatures=temperatures,air=air,paired=paired,short=short,lw=lw,
                    vapor=vapor,traffic=traffic,canopy=canopy,air_storage=air_storage,
                    ground_storage=ground_storage,conduction=conduction,sensible=sensible)
            count=0
            def fun(x):
                nonlocal count
                count+=1
                return evaluate(x,trial=True)[0]
            guess=np.r_[old['air_c'],old['ground_c']]
            for radiation_iterations in range(1,51 if net is not None else 2):
                fixed_emission=net.emission(base,radiation_temperatures,[(1.,20.)]) if net is not None else None
                solution=root(fun,guess,options={'xtol':1e-8,'maxfev':max_evaluations})
                residual,data=evaluate(solution.x)
                if np.max(np.abs(residual))>balance_tolerance_w_m2:
                    raise RuntimeError(f'Unconverged HOY {c.HOY[h]}: {residual.tolist()}; {solution.message}')
                if net is None:break
                updated=data['surface_temperatures'] if binary_engine else [{s['name']:s['exterior_c'] for s in b['hours'][0]['surfaces']} for b in data['building']]
                new_ir,new_ground_ir,E,incoming=net.longwave_boundary(base,updated,list(zip(weights,solution.x[1:])),skyir,float(solution.x[0]))
                old_ir=[p['hours'][0]['network_ir_w_m2'] for p in base]
                radiation_error=max(max(abs(np.asarray(a)-b)) for a,b in zip(new_ir,old_ir))
                if radiation_error<=.01:break
                radiation_temperatures=[{k:(1-longwave_relaxation)*a[k]+longwave_relaxation*b[k] for k in a} for a,b in zip(radiation_temperatures,updated)]
                guess=solution.x
            else:raise RuntimeError(f'Longwave exchange failed at HOY {c.HOY[h]}: {radiation_error} W/m2')
            if binary_engine:
                residual,data=evaluate(solution.x,full=True)
                if np.max(np.abs(residual))>balance_tolerance_w_m2:raise RuntimeError('Final response residual differs')
                engine.end_step()
            if net is not None:net.refresh(int(c.HOY[h]),E)
            ta=float(solution.x[0]);tg=solution.x[1:]
            next_state={'air_c':ta,'ground_c':tg.tolist(),
                'buildings':[b['final_state'] for b in data['building']],
                'last_hoy':int(c.HOY[h]),'last_shortwave':short.tolist(),'signature':signature}
            if net is not None:next_state['radiation_surface_c']=updated
            total_res=float(residual[0]+fractions@residual[1:])+sum(b['hours'][0]['balance_residual_w'] for b in data['building'])/area
            row={'hoy':int(c.HOY[h]),'Tair':ta,'Tsurface':float(weights@tg),'Tambient':tb,
                 'QS':float(fractions@short),'QL':float(fractions@data['lw']),
                 'QV':data['vapor'],'QA':data['air']+traffic,
                 'building_to_air_w_m2':data['air'],'building_to_ground_w_m2':data['paired'],
                 'building_shortwave_in_uhi_w_m2':0.0,'roof_fraction_in_uhi':0.0,
                 'total_residual_w_m2':total_res,'max_residual_w_m2':float(np.max(np.abs(residual))),
                 'evaluations':count,'canopy_to_background_w_m2':data['canopy'],
                 'air_storage_w_m2':data['air_storage'],'ground_storage_w_m2':float(fractions@data['ground_storage']),
                 'ground_to_deep_w_m2':float(fractions@data['conduction'])}
            if net is not None:
                group_direct=net.aggregate(net.direct[:,net.lookup[int(c.HOY[h])]])
                group_reflected=net.aggregate(net.reflected[:,net.lookup[int(c.HOY[h])]])
                ignored=0.;absorbed_pair=0.;building_net_lw=0.
                for b,p in enumerate(base):
                    building_net_lw+=data['building'][b]['hours'][0]['longwave_to_network_w']
                    for face in p['surfaces']:
                        if face['kind']=='floor':continue
                        g=2*b+int(face['kind'] in ('roof','skylight'))
                        absorbed=face['area_m2']*face['emissivity']*incoming[g]
                        if face['kind'] in ('window','skylight') and net.meta['ignore_window_longwave']:ignored+=absorbed
                        else:absorbed_pair+=absorbed
                absorbed_pair+=net.group_area[-1]*eps[0]*incoming[-1]
                sent_pair=float(np.sum(net.group_area*E*net.view_sum))
                row.update(radiation_iterations=radiation_iterations,radiation_error_w_m2=radiation_error,
                    ground_primary_sw_w_m2=float(group_direct[-1]),ground_reflected_sw_w_m2=float(group_reflected[-1]),
                    ignored_window_lw_w=ignored,longwave_pair_received_w=sent_pair,
                    radiation_exchange_residual_w_m2=float((building_net_lw+net.group_area[-1]*eps[0]*(sum(w*SIGMA*(t+273.15)**4 for w,t in zip(weights,tg))-(incoming[-1]+net.group_sky[-1]*skyir+net.far[-1]*SIGMA*(ta+273.15)**4))-np.sum(net.group_area*(E*(1-net.view_sum)-(net.group_sky*skyir+net.far*SIGMA*(ta+273.15)**4)))-ignored)/area),
                    building_net_lw_w=building_net_lw)
            for j,name in enumerate(NAMES):
                row['Tg'+name.title()]=float(tg[j]);row['Ta'+name.title()]=ta
                c.Tg_split[name][h]=tg[j];c.Ta_split[name][h]=ta
                c.QS[name][h]=short[j];c.QL[name][h]=data['lw'][j]
                c.QHFlux[name][h]=data['sensible'][j];c.QGFlux[name][h]=data['conduction'][j]
                c.Qsg[name][h]=data['ground_storage'][j]
                c.QVFlux[name][h]=-data['canopy'];c.Qsa[name][h]=-data['air_storage']
            c.Ta[h]=ta;c.Tg[h]=row['Tsurface']
            return next_state,row,[b['hours'][0] for b in data['building']]

        if initial_state is None and preheat:
            converged=False
            for cycle in range(len(warmup_history)+1,max_preheat+1):
                before=copy.deepcopy(state)
                for h in range(24):state,_,_=step(climates[0],h,templates[0],state)
                change=max(abs(state['air_c']-before['air_c']),np.max(np.abs(np.array(state['ground_c'])-before['ground_c'])))
                previous_buildings=[v if v is not None else [p['initial_c']]*len(current) for v,p,current in zip(before['buildings'],templates[0],state['buildings'])]
                change=max(change,max(np.max(np.abs(np.array(a)-b)) for a,b in zip(state['buildings'],previous_buildings)))
                warmup_history.append({'cycle':cycle,'max_state_change_k':float(change)})
                # Accelerate only the initial guess. Acceptance always uses the full,
                # unaccelerated 24-hour physical map and its actual state residual.
                lengths=[len(v) for v in state['buildings']]
                vector=np.r_[state['air_c'],state['ground_c'],np.concatenate(state['buildings'])]
                warmup_vectors.append(vector.copy())
                accepted=cycle>=preheat and change<=warmup_tolerance_k
                if warmup_acceleration and not accepted and len(warmup_vectors)>=3 and cycle%3==0:
                    a,b,c=warmup_vectors[-3:];d1=b-a;d2=c-b;den=d2-d1
                    mask=(abs(den)>1e-9)&(abs(d1)>1e-9)&(d1*d2>0)&(abs(d2)<abs(d1))
                    guess=c.copy();guess[mask]=a[mask]-d1[mask]**2/den[mask]
                    guess=np.clip(guess,c-10,c+10);guess=np.clip(guess,-80,120)
                    state['air_c']=float(guess[0]);state['ground_c']=guess[1:4].tolist()
                    offset=4
                    for j,length in enumerate(lengths):state['buildings'][j]=guess[offset:offset+length].tolist();offset+=length
                    # Radiation is a solved boundary, not a separately stored capacity.
                    state.pop('radiation_surface_c',None)
                    warmup_history[-1]['next_guess_accelerated']=True
                if output_dir is not None:
                    checkpoint=Path(output_dir);checkpoint.mkdir(parents=True,exist_ok=True)
                    temporary=checkpoint/'warmup_checkpoint.json.tmp'
                    temporary.write_text(json.dumps({'state':state,'history':warmup_history},allow_nan=False))
                    temporary.replace(checkpoint/'warmup_checkpoint.json')
                if show_progress:print(f'Warmup {cycle}: {change:.5f} K',flush=True)
                if accepted:
                    converged=True;break
            if not converged:raise RuntimeError(f'Warmup did not converge in {max_preheat} cycles')
        report_initial_state=None
        for day,c in enumerate(climates):
            if day==spinup_days:report_initial_state=copy.deepcopy(state)
            for h in range(24):
                state,row,outs=step(c,h,templates[day],state)
                all_rows.append(row)
                if show_progress and h%6==0:print(f'HOY {int(c.HOY[h])}: LW iterations={row.get("radiation_iterations",1)}, residual={row["max_residual_w_m2"]:.3g} W/m2',flush=True)
                for b,out in enumerate(outs):building_rows[b].append(out)
            if show_progress:print(f'Coupled day {day+1}/{len(climates)} complete',flush=True)
    spinup_info={'days':spinup_days,'hours':spinup_days*24,'start_hoy':int(hoy[0]),'report_start_hoy':int(hoy[spinup_days*24])}
    if spinup_days:
        climates=climates[spinup_days:];all_rows=all_rows[spinup_days*24:]
        building_rows=[rows[spinup_days*24:] for rows in building_rows];hoy=hoy[spinup_days*24:]
    # Write state/output before committing caller objects, so failed persistence is explicit.
    if net is not None:
        net.save()
    metadata={'engine':'cetus-surface-7-reconstructed','implementation':'8.3','engine_protocol':engine_protocol,'exposed_fractions':dict(zip(NAMES,fractions.tolist())),
        'warmup':warmup_history,'warmup_method':'periodic first forcing day; Aitken guesses, full-day residual acceptance' if warmup_acceleration else 'periodic first forcing day','chronological_spinup':spinup_info,'report_initial_state':report_initial_state,'final_state':state,'assumptions':audits[0],'radiation_network':net.meta if net is not None else None,
        'green_roofs':'Green-roof evaporation is disabled in this coupled path; no roof moisture model.',
        'ground_receiver':'Area-weighted exposed ground temperature used by buildings; returned ground exchanges distributed by exposed area.',
        'latent':'Existing UHI evaporation remains a prescribed canopy sensible-energy sink; humidity is not prognostic.'}
    if output_dir is not None:
        import pandas as pd
        folder=Path(output_dir);folder.mkdir(parents=True,exist_ok=True)
        pd.DataFrame(all_rows).to_csv(folder/'hourly.csv',index=False)
        (folder/'state.json').write_text(json.dumps(state,indent=2,allow_nan=False))
        (folder/'audit.json').write_text(json.dumps(metadata,indent=2,allow_nan=False))
        (folder/'building_hourly.json').write_text(json.dumps(building_rows,allow_nan=False,separators=(',',':')))
    for old,new in zip(neighborhood.climate[spinup_days:],climates):
        for field in old.__slots__:setattr(old,field,copy.deepcopy(getattr(new,field)))
    if spinup_days:neighborhood.climate=neighborhood.climate[spinup_days:]
    for b,rows in zip(neighborhood.buildings,building_rows):
        b.energyModel='surface-v7';b.energyHOY=hoy.tolist()
        b.energyFeedback={'surface_v7':rows}
        b.coolingLoad=np.array([max(-r['hvac_w'],0) for r in rows])
        b.heatingLoad=np.array([max(r['hvac_w'],0) for r in rows])
        b.ACLoad=b.coolingLoad-b.heatingLoad
        b.ACWasteHeat=np.array([r['convection_to_air_w']+r['ventilation_to_air_w']+r['hvac_to_air_w'] for r in rows])
    for d,c in enumerate(neighborhood.climate):
        c.ACLoad=sum(b.ACLoad[d*24:(d+1)*24] for b in neighborhood.buildings)
        c.ACWasteHeat=sum(b.ACWasteHeat[d*24:(d+1)*24] for b in neighborhood.buildings)
        c.coolingLoad=c.ACWasteHeat/area
    keys=['QS','QL','QV','QA','Tair','Tsurface','Tambient','TaGround','TaWater','TaLand','TgGround','TgWater','TgLand']
    result={key:np.array([r[key] for r in all_rows]).reshape(-1,24).T for key in keys}
    result['coupled']=metadata;result['hourly']=all_rows
    return result
