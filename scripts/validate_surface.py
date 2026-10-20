"""Re-run recovered four-case July inputs, with a causal thermal network.

Recovered input already contains effective transmitted and inward glass gains,
so this regression tests dynamic envelope/loads, not the full optical model.
No parameter fitting or daily redistribution is performed.
"""
import json
import gzip
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ENERGY.surface import SurfaceEngine
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'validation/ashrae_recovered'
OUT=ROOT/'validation/rebuilt_results'


def convert(old):
    surfaces=[]
    for s in old['surfaces']:
        name=s['name'];win=name=='window';floor=name=='floor'
        # Illustrative light BESTEST construction capacities; source R retained.
        layers={
            'wall':[{'resistance_m2k_w':.064,'capacity_j_m2k':4293},
                    {'resistance_m2k_w':s['resistance']-.139,'capacity_j_m2k':665},
                    {'resistance_m2k_w':.075,'capacity_j_m2k':9576}],
            'roof':[{'resistance_m2k_w':.136,'capacity_j_m2k':9063},
                    {'resistance_m2k_w':s['resistance']-.1985,'capacity_j_m2k':1127},
                    {'resistance_m2k_w':.0625,'capacity_j_m2k':7980}],
            'floor':[{'resistance_m2k_w':s['resistance']-.179,'capacity_j_m2k':0},
                     {'resistance_m2k_w':.179,'capacity_j_m2k':19500}]
        }
        surfaces.append(dict(name=name,kind=name,area_m2=s['area'],resistance_m2k_w=s['resistance'],
            thermal_mass_j_m2k=0,reflectance=1-s['absorptance'],transmittance=0,emissivity=.84 if win else .9,
            hi_w_m2k=s['hi'],ho_w_m2k=0 if floor else max(.1,s['ho']-5.13),
            sky_view=0 if floor else s['sky_view'],ground_view=0 if floor else 1-s['sky_view'],
            ground_contact_w_m2k=s['ho'] if floor else 0,absorbed_inside_fraction=0,
            **({'layers':layers[name]} if not win else {})))
    hours=[]
    for h in old['hours']:
        hours.append(dict(air_c=h['air_c'],ground_c=h['boundary_c'][2],sky_ir_w_m2=h['sky_ir_w_m2'],
            irradiance_w_m2=h['irradiance_w_m2'],internal_w=h['internal_w'],
            additional_transmitted_w=h['solar_transmitted_w'],additional_glass_inward_w=h['solar_absorbed_inward_w'],
            heat_setpoint_c=h['heat_setpoint_c'],cool_setpoint_c=h['cool_setpoint_c'],
            ventilation_w_k=old['density_kg_m3']*old['cp_j_kg_k']*old['volume_m3']*old['infiltration_ach']/3600))
    return dict(protocol='cetus-surface-7',dt_s=3600,surfaces=surfaces,hours=hours,initial_c=20,
        cooling_cop=1,heating_cop=1,internal_radiant_fraction=.6,solar_floor_fraction=.642)


def main():
    OUT.mkdir(exist_ok=True)
    cases=[600,660,670,680];limits=[.1921,.1697,.3018,.2097]
    originals=[json.loads(gzip.decompress((DATA/f'R5_case{c}.json.gz').read_bytes())) for c in cases]
    inputs=[convert(p) for p in originals]
    with SurfaceEngine() as engine:
        cycles=[{**p,'hours':p['hours'][:24]} for p in inputs]
        for cycle in range(30):
            result=engine.request(cycles)
            if cycle:
                change=max(np.max(np.abs(np.array(o['final_state'])-p['initial_state'])) for o,p in zip(result,cycles))
                if change<.001:break
            for o,p in zip(result,cycles):p['initial_state']=o['final_state']
        else:raise RuntimeError('Benchmark warmup failed')
        for p,o in zip(inputs,result):p['initial_state']=o['final_state']
        results=engine.request(inputs)
    reference=pd.read_csv(DATA/'reference_july14.csv')
    rows=[];hourly=[]
    for c,limit,p,old,out in zip(cases,limits,inputs,originals,results):
        selected=[i for i,h in enumerate(old['hours']) if 4657<=h['hour_ending']<=4680]
        pred=np.array([out['hours'][i]['hvac_w']/1000 for i in selected]);ref=reference[reference['case']==c]
        truth=ref.reference_signed_kwh.to_numpy();r5=ref.model_signed_kwh.to_numpy()
        rmse=float(np.sqrt(np.mean((pred-truth)**2)));pcc=float(np.corrcoef(pred,truth)[0,1])
        rows.append(dict(case=c,rmse_kwh=rmse,rmse_limit_kwh=limit,pcc=pcc,
            passed=bool(rmse<=limit and pcc>=.879),r5_rmse_kwh=float(np.sqrt(np.mean((r5-truth)**2))),
            max_balance_residual_w=max(abs(o['balance_residual_w']) for o in out['hours']),hours=len(out['hours'])))
        for h,(a,b,d) in enumerate(zip(truth,r5,pred),1):hourly.append(dict(case=c,hour_ending=h,reference_kwh=float(a),r5_kwh=float(b),reconstructed_v7_kwh=float(d)))
        with gzip.GzipFile(str(OUT/f'case{c}_input.json.gz'),'wb',mtime=0) as f:
            f.write(json.dumps(p,separators=(',',':')).encode())
    pd.DataFrame(rows).to_csv(OUT/'metrics.csv',index=False);pd.DataFrame(hourly).to_csv(OUT/'hourly.csv',index=False)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,2,figsize=(11,7),layout='constrained')
    for ax,c in zip(axes.flat,cases):
        x=pd.DataFrame(hourly);x=x[x['case']==c]
        for key,label,style in [('reference_kwh','Reference','k--'),('r5_kwh','R5','C1-'),('reconstructed_v7_kwh','Reconstructed v7','C0-')]:ax.plot(x.hour_ending,x[key],style,label=label)
        ax.set(title=f'Case {c}',xlabel='Hour ending',ylabel='Signed thermal load (kWh)');ax.grid(alpha=.2);ax.legend()
    fig.savefig(OUT/'comparison.png',dpi=150)
    print(json.dumps({'warmup_cycles':cycle+1,'metrics':rows},indent=2))

if __name__=='__main__':main()
