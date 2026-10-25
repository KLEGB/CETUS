"""Whole-period city/building Picard iteration, using the 9.2 surface engine.

N = one city pass without simulated building feedback, then one building pass.
F = repeat a full city pass and a full building pass, each restarted from the
same physical initial condition. This is distinct from ENERGY.coupled's
simultaneous hourly root solve; its default API and convergence are untouched.
"""
from __future__ import annotations

import copy
import json
import warnings
from pathlib import Path

import numpy as np
from scipy.optimize import root

from ENERGY.coupled import _fractions
from ENERGY.surface import build_surface_input, SIGMA
from ENERGY.surface_binary import BinarySurfaceEngine
from UHI.aT import referenceVelocity, combinedConvectionCoefficient, airDensity
from UHI.vapor import vaporHeatFlowIntensity
from ENERGY.result_store import ResultWriter, export_json
from .model import write_json


def settings(state):
    iteration=str(state.get('iteration','x')).strip().lower()
    if iteration!='x' and (not iteration.isdigit() or not 1<=int(iteration)<=10):
        raise ValueError('iteration 必须为 1–10 或 x')
    sample=float(state.get('sample',.3));tol=float(state.get('load_tolerance',.1))
    if not np.isfinite([sample,tol]).all() or not 0<sample<=1 or tol<=0:
        raise ValueError('sample 必须在 (0,1]；负荷收敛阈值必须大于 0')
    return iteration,sample,tol


def ensure_network(n,folder,sample):
    """Accept only current point-network containers, never legacy exposure files."""
    if n._rad:
        n.radSimulation(radFile=n._rad,output_path=folder/'network.rad.npz')
        net=n._radiation_network
        for c in n.climate:
            if not all(int(h) in net.lookup for h in c.HOY):
                raise ValueError('RAD 文件未覆盖当前计算日期，请清空 RAD 后重新建网')
    else:
        kwargs={}
        if not n._epw:
            # CSV has no direct-normal radiation. Match the existing portable
            # sky's isotropic diffuse + nearest sun patch approximation.
            from WEA.epw import load_sun_vectors, TREGENZA_COEFFICIENTS, TREGENZA_PATCHES_PER_ROW
            rays=load_sun_vectors();rays=rays/np.linalg.norm(rays,axis=1)[:,None]
            omega=np.repeat(TREGENZA_COEFFICIENTS,TREGENZA_PATCHES_PER_ROW)
            matrix=np.zeros((len(rays),8760))
            for c in n.climate:
                for h,hoy in enumerate(c.HOY.astype(int)):
                    hour=h+.5;day=hoy//24+1
                    dec=np.deg2rad(23.45*np.sin(2*np.pi*(284+day)/365))
                    lat=np.deg2rad(n.location.latitude);angle=np.deg2rad(15*(hour-12))
                    sun=np.array([-np.cos(dec)*np.sin(angle),np.cos(lat)*np.sin(dec)-np.sin(lat)*np.cos(dec)*np.cos(angle),
                                  np.sin(lat)*np.sin(dec)+np.cos(lat)*np.cos(dec)*np.cos(angle)])
                    ghi=max(0,float(c.RadTotal[h]));dhi=min(ghi,max(0,float(c.RadDiffuse[h])))
                    col=dhi*omega/np.pi
                    if sun[2]>0:col[np.argmax(rays@sun)]+=max(0,ghi-dhi)/sun[2]
                    vertical=float(np.maximum(0,rays[:,2])@col)
                    if vertical>0:col*=ghi/vertical
                    matrix[:,hoy]=col/1000
            kwargs['sky_matrix']=matrix
            print('CSV 建网采用各向同性散射与最近太阳天空片近似；位置由气象设置提供。',flush=True)
        n.radSimulation(sample=sample,output_path=folder/'network.rad.npz',**kwargs)
    return n._radiation_network


def model_inputs(n,net,climates):
    hoy=np.concatenate([c.HOY.astype(int) for c in climates])
    solar=net.solar(hoy)
    primary=net.aggregate(net.direct[:,[net.lookup[int(h)] for h in hoy]])
    reflected=net.aggregate(net.reflected[:,[net.lookup[int(h)] for h in hoy]])
    days=[];audits=[]
    for d,c in enumerate(climates):
        daily=[];audit=[];sl=slice(d*24,(d+1)*24)
        for i,b in enumerate(n.buildings):
            irr=dict(facade=solar[2*i,sl],roof=solar[2*i+1,sl],
                     facade_primary=primary[2*i,sl],roof_primary=primary[2*i+1,sl],
                     facade_reflected=reflected[2*i,sl],roof_reflected=reflected[2*i+1,sl])
            payload,a=build_surface_input(b,c,irradiance=irr,options=n.energy_options)
            # The generic surface builder uses Tb. The editor explicitly uses
            # the city pass's Ta as the Energy air/ventilation boundary.
            for h,hour in enumerate(payload['hours']):
                old=float(hour['air_c']);new=float(c.Ta[h])
                hour['air_c']=new
                hour['ventilation_w_k']*=(old+273.15)/(new+273.15)
            daily.append(payload);audit.append(a)
        days.append(daily);audits.append(audit)
    return days,audits


def buildings_pass(n,net,climates,folder,mode,previous,report):
    days,audits=model_inputs(n,net,climates)
    count=len(n.buildings);fractions=_fractions(n.site);weights=fractions/fractions.sum()
    folder.mkdir(parents=True,exist_ok=True)
    archive=ResultWriter(folder/'building_results.bin',[b.input_dict() for b in n.buildings],
                         [{k:v for k,v in p.items() if k!='hours'} for p in days[0]],
                         dict(mode=mode,options=n.energy_options,source_climate=[c.to_dict() for c in climates]))
    states=[None]*count;totals=[];warmup=[]
    spectral=None
    if n.energy_options.get('longwave',{}).get('enabled',False):
        from RAD.spectral import SpectralNetwork
        eps=np.repeat(net.meta['ground_emissivity'],3)
        spectral=SpectralNetwork(net,days[0],n.energy_options['longwave'],eps,weights)
        for day in days[1:]:
            for model,first in zip(day,days[0]):
                for face,source in zip(model['surfaces'],first['surfaces']):
                    if 'longwave_bands' in source:face['longwave_bands']=copy.deepcopy(source['longwave_bands'])
    with archive, BinarySurfaceEngine() as engine:
        engine.configure(days[0])
        def run_day(d,record):
            nonlocal states
            c=climates[d]
            for h in range(24):
                index=d*24+h
                models=[dict(p,hours=[copy.deepcopy(p['hours'][h])]) for p in days[d]]
                for i,p in enumerate(models):
                    if states[i] is not None:p['initial_state']=states[i]
                temps=previous[index]['temperatures'] if previous else [
                    {s['name']:float(c.Tb[h]) for s in p['surfaces']} for p in models]
                ground=[(float(w),float(c.Tg_split[k][h])) for w,k in zip(weights,('ground','land','water'))]
                sky=models[0]['hours'][0]['sky_ir_w_m2'];ta=float(c.Ta[h])
                if spectral:
                    ir,_,_,_=spectral.response(temps,float(c.Tb[h]),sky).boundary(ground,ta)
                else:ir,_,_,_=net.longwave_boundary(models,temps,ground,sky,ta)
                for p,boundary in zip(models,ir):p['hours'][0]['network_ir_w_m2']=boundary
                engine.begin_step(models)
                batch=engine.evaluate([p['hours'][0] for p in models],response_level='full',return_state=True,dynamic_fields=())['batch']
                states=[b['final_state'] for b in batch];engine.end_step()
                if not record:continue
                rows=[b['hours'][0] for b in batch]
                extras=[]
                for i,row in enumerate(rows):
                    light=float(audits[d][i]['lighting_electricity_w'][h])
                    extra=dict(lighting_electricity_w=light,hvac_plus_lighting_electricity_w=row['electricity_w']+light)
                    row.update(extra);extras.append(extra)
                archive.record(int(c.HOY[h]),raw=engine.last_raw_result,extras=extras,applied_inputs=[p['hours'][0] for p in models])
                total=dict(
                    air=sum(r['convection_to_air_w']+r['ventilation_to_air_w']+r['hvac_to_air_w'] for r in rows),
                    contact=sum(r['contact_to_ground_w'] for r in rows),
                    load=sum(max(0,-r['hvac_w']) for r in rows),
                    net_load=sum(-r['hvac_w'] for r in rows),
                    energy=sum(r['hvac_plus_lighting_electricity_w'] for r in rows)/1000,
                    temperatures=[{s['name']:s['exterior_c'] for s in r['surfaces']} for r in rows])
                totals.append(total)
            if record:report(f'{mode} 建筑：完成第 {d+1}/{len(days)} 天')
        for cycle in range(120):
            before=copy.deepcopy(states);run_day(0,False)
            change=max(float(np.max(np.abs(np.asarray(a)-b))) for a,b in zip(states,before)) if before[0] is not None else None
            warmup.append(dict(cycle=cycle+1,change_k=change))
            if cycle>=3 and change is not None and change<=.1:break
        else:warnings.warn('建筑周期预热达到 120 轮，继续正式计算',RuntimeWarning)
        for d in range(len(days)):run_day(d,True)
        path=archive.finish(dict(mode=mode,warmup=warmup,implementation='9.2.0',coupling='whole-period Picard'))
    return totals,path


def city_pass(n,net,feedback,report):
    """Conservative city balance with frozen full-period building exchanges.

    Same air/three-ground residuals and roof-exclusion ledger as coupled.py;
    building sensible/contact sources and exterior LW are held to the previous
    full building pass rather than re-solved inside every city root evaluation.
    """
    climates=copy.deepcopy(n.climate);s=n.site;cfg=n.setting
    if cfg.dt is None or float(cfg.dt)!=3600:raise ValueError('逐时模拟要求 dt=3600 秒')
    fractions=_fractions(s);exposed=float(fractions.sum());weights=fractions/exposed
    area=float(s.TotalArea);cap=np.array([cfg.CGround,cfg.CLand,cfg.CWater])
    deep=np.array([cfg.UDeepGround,cfg.UDeepLand,cfg.UDeepWater])
    eps=np.repeat(net.meta['ground_emissivity'],3)
    vapor_site=copy.deepcopy(s);vapor_site.greenRoofPercentage=0
    initial=float(climates[0].Tb[0]);state=np.full(4,initial);last_short=np.zeros(3)
    templates=[build_surface_input(b,climates[0],options=n.energy_options)[0] for b in n.buildings]
    spectral=None
    if n.energy_options.get('longwave',{}).get('enabled',False):
        from RAD.spectral import SpectralNetwork
        spectral=SpectralNetwork(net,templates,n.energy_options['longwave'],eps,weights)
    rows=[]
    def day(d,record):
        nonlocal state,last_short
        c=climates[d]
        for h in range(24):
            idx=d*24+h;hoy=int(c.HOY[h]);old=state.copy()
            c.Ta[h-1]=old[0]
            for j,k in enumerate(('ground','land','water')):
                c.Tg_split[k][h-1]=old[j+1];c.QS[k][h-1]=last_short[j]
            short=np.repeat((1-net.meta['ground_reflectance'])*net.solar([hoy])[-1,0],3)
            sky=SIGMA*(float(c.SkyTemperature[h])+273.15)**4;tb=float(c.Tb[h])
            temps=feedback[idx]['temperatures'] if feedback else [
                {face['name']:tb for face in p['surfaces']} for p in templates]
            air=feedback[idx]['air']/area if feedback else 0.
            contact=feedback[idx]['contact']/area if feedback else 0.
            ref=referenceVelocity(float(c.Velocity[h]),s.kS,cfg.a,cfg.k)
            pressure=float(c.Pressure[h])
            traffic=(cfg.annualDistance*cfg.FE*cfg.fuelDensity*cfg.NHC/365/24/3600*s.AreaRoad*cfg.intensity/area)
            response=spectral.response(temps,tb,sky) if spectral else None
            def evaluate(x):
                ta=float(x[0]);tg=x[1:]
                if not np.isfinite(x).all() or min(x)<-100 or max(x)>180:raise RuntimeError('城市求解超出物理温度范围')
                ground=list(zip(weights,tg))
                if spectral:
                    _,ground_ir,_,_=response.boundary(ground,ta);lw=ground_ir
                else:
                    _,ground_ir,_,_=net.longwave_boundary(templates,temps,ground,sky,ta)
                    lw=eps*(ground_ir-SIGMA*(tg+273.15)**4)
                hc=np.array([combinedConvectionCoefficient(float(t),ta,pressure,ref,cfg.c1,cfg.zRef,cfg.z0) for t in tg])
                sensible=hc*(tg-ta);c.Ta[h]=ta
                vapor=float(vaporHeatFlowIntensity(h,c,vapor_site,cfg))
                canopy=combinedConvectionCoefficient(tb,ta,pressure,float(c.Velocity[h]),cfg.c1,cfg.zRefd,cfg.z0d)*(ta-tb)
                storage_air=airDensity(pressure,ta)*1006*cfg.HAir*(ta-old[0])/3600
                storage_ground=cap*(tg-old[1:])/3600;conduction=deep*(tg-cfg.GroundTemperature)
                rg=short+lw+contact/exposed-sensible-conduction-storage_ground
                rg=np.where(fractions>0,rg,tg-old[1:])
                ra=float(fractions@sensible)+air+traffic+vapor-canopy-storage_air
                return np.r_[ra,rg],dict(QS=float(fractions@short),QL=float(fractions@lw),QV=vapor,QA=air+traffic,
                    QHFlux=float(fractions@sensible),QGFlux=float(fractions@conduction),
                    Qsa=storage_air,Qsg=float(fractions@storage_ground),canopy=canopy,
                    _groups=dict(QL=lw,QHFlux=sensible,QGFlux=conduction,Qsg=storage_ground))
            solution=root(lambda x:evaluate(x)[0],old,options={'xtol':1e-8,'maxfev':120})
            residual,flux=evaluate(solution.x)
            group_flux=flux.pop('_groups')
            if np.max(np.abs(residual))>.02:raise RuntimeError(f'城市 HOY {hoy} 热平衡未收敛：{residual.tolist()}')
            state=solution.x
            last_short=short.copy()
            c.Ta[h]=state[0];c.Tg[h]=float(weights@state[1:])
            for j,k in enumerate(('ground','land','water')):
                c.Ta_split[k][h]=state[0];c.Tg_split[k][h]=state[j+1]
                c.QS[k][h]=short[j]
                # Keep the city diagnostics in Neighborhood, not only in plots.
                for field,values in group_flux.items():getattr(c,field)[k][h]=values[j]
                c.QA[k][h]=flux['QA'];c.Qsa[k][h]=-flux['Qsa'];c.QVFlux[k][h]=-flux['canopy']
            if record:rows.append(dict(hoy=hoy,Ta=float(c.Ta[h]),Tg=float(c.Tg[h]),Tb=tb,
                            max_residual=float(np.max(np.abs(residual))),**flux))
        if record:report(f'城市：完成第 {d+1}/{len(climates)} 天')
    for cycle in range(120):
        old=state.copy();day(0,False)
        if cycle>=3 and np.max(np.abs(state-old))<=.1:break
    else:warnings.warn('城市周期预热达到 120 轮，继续正式计算',RuntimeWarning)
    for d in range(len(climates)):day(d,True)
    return climates,rows


def run_comparison(n,folder,report=print):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    iteration,sample,tol=settings(n.view_state)
    if not n.buildings or not n.view_state.get('weather_loaded'):raise ValueError('请先导入建筑和气象')
    if not n._shp or not Path(n._shp).is_file():raise ValueError('计算需要与 Neighborhood 对应的 SHP 文件')
    gfa=sum(b.GFA for b in n.buildings)
    if gfa<=0:raise ValueError('总 GFA 必须大于 0')
    report('校验或建立当前版本 RAD 网络…')
    net=ensure_network(n,folder,sample)
    report('N：先计算完整时段 UHI，再计算建筑，不回传建筑反馈')
    nc,nrows=city_pass(n,net,None,report)
    feedback,npath=buildings_pass(n,net,nc,folder/'N','N',None,report)
    def attach(rows,loads):
        return [dict(row,Load=load['load']/gfa,TotalEnergy=load['energy']/gfa) for row,load in zip(rows,loads)]
    nrows=attach(nrows,feedback);history=[];converged=False
    count=10 if iteration=='x' else int(iteration)
    previous_load=np.array([r['load']/gfa for r in feedback])
    for cycle in range(1,count+1):
        report(f'F：完整时段交替计算，第 {cycle}/{count} 轮')
        fc,frows=city_pass(n,net,feedback,report)
        fresh,fpath=buildings_pass(n,net,fc,folder/'F' / f'iteration_{cycle:02d}','F',feedback,report)
        new_load=np.array([r['load']/gfa for r in fresh])
        error=float(np.max(np.abs(new_load-previous_load)))
        history.append(dict(iteration=cycle,load_change_w_m2_gfa=error,
                            city_residual_w_m2=max(row['max_residual'] for row in frows)))
        report(f'F 第 {cycle} 轮：最大逐时负荷变化 {error:.6g} W/m² GFA；阈值 {tol:g}')
        write_json(folder/'progress.json',dict(history=history))
        feedback=fresh;previous_load=new_load;converged=error<=tol
        if iteration=='x' and converged:break
    if not converged:report(f'提示：结束时负荷变化尚未达到阈值，结果标记为未收敛（{cycle} 轮）。')
    frows=attach(frows,feedback)
    for d,c in enumerate(fc):
        daily=feedback[d*24:(d+1)*24]
        c.ACLoad=np.array([r['net_load'] for r in daily])
        c.ACWasteHeat=np.array([r['air'] for r in daily])
        c.coolingLoad=c.ACWasteHeat/float(n.site.TotalArea)
    n.climate=fc;n.building_result_file=fpath
    paths={}
    for mode,path in [('N',npath),('F',fpath)]:
        target=folder/f'{mode}_building_results.json'
        export_json(path,target);paths[mode]=dict(binary=str(path),json=str(target))
    import pandas as pd
    for mode,rows in [('N',nrows),('F',frows)]:pd.DataFrame(rows).to_csv(folder/f'{mode}_hourly.csv',index=False,encoding='utf-8-sig')
    result=dict(N=nrows,F=frows,history=history,converged=converged,iterations=cycle,
                load_tolerance=tol,total_gfa=gfa,paths=paths,
                energy_kwh_m2={mode:sum(r['TotalEnergy'] for r in rows) for mode,rows in [('N',nrows),('F',frows)]},
                units=dict(Load='W/m² GFA',TotalEnergy='kWh/m² GFA per hour',heat_flux='W/m² site'),
                definition='N: city → buildings; F: whole-period city ↔ buildings; max hourly cooling-load difference')
    write_json(folder/'results.json',result)
    n.saveJSON(folder/'completed_neighborhood.json')
    report(f'完成。N 解包结果：{paths["N"]["json"]}')
    report(f'F 解包结果：{paths["F"]["json"]}')
    report('HOY  Ta(°C)  Tg(°C)  Tb(°C)  Load(W/m² GFA)  TotalEnergy(kWh/m² GFA)')
    for r in frows:report(f'{r["hoy"]:04d}  {r["Ta"]:.3f}  {r["Tg"]:.3f}  {r["Tb"]:.3f}  {r["Load"]:.4f}  {r["TotalEnergy"]:.6f}')
    return result
