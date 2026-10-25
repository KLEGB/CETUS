"""Single thermal zone with scalar envelope areas and optional room lighting.

No SHP, polygon, window positions, or room geometries are required. Hourly
facade/roof shortwave arrays are incident broadband W/m2, before glazing.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import numpy as np
from .surface import build_surface_input
from .surface_binary import BinarySurfaceEngine
from .result_store import ResultWriter


def simulate_surface(building, climates, *, irradiance_by_day=None, options=None,
                     preheat=4, max_preheat=120, warmup_tolerance_k=.1,
                     initial_state=None, output_dir=None, executable_path=None):
    """Run consecutive complete days, returning thermal and lighting meters.

    Scalar ExteriorWallArea/ExteriorWindowArea refer to the typical floor;
    RoofArea/GroundFloorArea/SkylightArea are whole-building areas. The normal
    CETUS object with a perimeter follows its existing whole-envelope path.
    An initial state is the engine temperature vector; use preheat=0 on restart.
    """
    climates=list(climates)
    if not climates:
        raise ValueError('At least one complete forcing day is required')
    hoy=np.concatenate([np.asarray(c.HOY,dtype=int) for c in climates])
    if np.any(np.diff(hoy)!=1):
        raise ValueError('Forcing days must be consecutive')
    if preheat<0 or max_preheat<preheat or warmup_tolerance_k<=0:
        raise ValueError('Invalid warmup limits')
    if initial_state is not None and preheat:
        raise ValueError('A restart requires preheat=0')
    irradiation=[None]*len(climates) if irradiance_by_day is None else list(irradiance_by_day)
    if len(irradiation)!=len(climates):
        raise ValueError('One irradiance mapping is required for each day')
    inputs=[]; audits=[]
    for c,irr in zip(climates,irradiation):
        p,a=build_surface_input(building,c,irradiance=irr,options=options)
        inputs.append(p);audits.append(a)
    import uuid,warnings
    folder=Path(output_dir) if output_dir is not None else Path.cwd()/'outputs'/'energy92'/uuid.uuid4().hex
    parameters=building.input_dict();parameters['building_id']=parameters.get('building_id') or 'B000000'
    archive=ResultWriter(folder/'building_results.bin',[parameters],
        [{k:v for k,v in inputs[0].items() if k!='hours'}],dict(options=options,day_audits=audits,source_climate=[c.to_dict() for c in climates]))
    state=copy.deepcopy(initial_state);history=[];rows=[]
    with archive, BinarySurfaceEngine(executable_path) as engine:
        engine.configure([inputs[0]])
        def run_day(p,a=None,c=None):
            nonlocal state
            for h,original in enumerate(p['hours']):
                request={**p,'hours':[copy.deepcopy(original)]}
                if state is not None:request['initial_state']=state
                engine.begin_step([request])
                out=engine.evaluate(request['hours'],response_level='full',return_state=True,dynamic_fields=())['batch'][0]
                state=out['final_state'];engine.end_step()
                if a is not None:
                    row=out['hours'][0];base=float(a['baseline_lighting_w'][h]);light=float(a['lighting_electricity_w'][h])
                    extras=dict(baseline_lighting_w=base,lighting_electricity_w=light,avoided_lighting_w=base-light,
                        lighting_schedule_ratio=light/base if base else 1.,hvac_plus_lighting_electricity_w=row['electricity_w']+light)
                    row.update(extras,hoy=int(c.HOY[h]));rows.append(row)
                    archive.record(c.HOY[h],raw=engine.last_raw_result,extras=[extras],applied_inputs=[original])
        if preheat:
            for cycle in range(max_preheat):
                before=copy.deepcopy(state);run_day(inputs[0])
                change=float(np.max(np.abs(np.asarray(state)-before))) if before is not None else None
                history.append(dict(cycle=cycle+1,change_k=change))
                if cycle+1>=preheat and change is not None and change<=warmup_tolerance_k:break
            else:warnings.warn(f'Warmup did not converge within {max_preheat} cycles; continuing simulation.',RuntimeWarning)
        for p,a,c in zip(inputs,audits,climates):run_day(p,a,c)
        summary={key:sum(r[field] for r in rows)/1000 for key,field in (
            ('baseline_lighting_kwh','baseline_lighting_w'),('lighting_electricity_kwh','lighting_electricity_w'),
            ('avoided_lighting_kwh','avoided_lighting_w'),('hvac_electricity_kwh','electricity_w'),
            ('hvac_plus_lighting_electricity_kwh','hvac_plus_lighting_electricity_w'))}
        result=dict(implementation='9.2.0',hours=rows,final_state=state,warmup=history,lighting_summary=summary,day_audits=audits)
        result['building_result_file']=archive.finish({k:v for k,v in result.items() if k!='hours'})
    return result
