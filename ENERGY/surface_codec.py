"""CES3 v1 schema and optional JSON <-> binary file adapter.

The hot path encodes native numeric values directly. JSON is only used for explicitly requested file conversion. Model registration
also uses the fixed binary schema.
"""
from __future__ import annotations
import argparse
import json
import struct
from pathlib import Path
import numpy as np

MAGIC=b'CES3'
HEADER=struct.Struct('<4sHHQQ')
MAX_FRAME=256 << 20
SCALARS=('air_c','ground_c','sky_ir_w_m2','internal_w','heat_setpoint_c','cool_setpoint_c','ventilation_w_k','additional_transmitted_w','additional_glass_inward_w')
ARRAYS=('irradiance_w_m2','ho_w_m2k','network_ir_w_m2')
FIELDS=SCALARS+ARRAYS
LEVELS={'totals':0,'coupling':1,'full':2}
MODEL_SCALARS=('dt_s', 'initial_c', 'cooling_cop', 'heating_cop', 'internal_radiant_fraction', 'solar_floor_fraction')
MODEL_SURFACE_FIELDS=('area_m2', 'resistance_m2k_w', 'thermal_mass_j_m2k', 'reflectance', 'transmittance', 'emissivity', 'hi_w_m2k', 'ho_w_m2k', 'sky_view', 'ground_view', 'ground_contact_w_m2k', 'absorbed_inside_fraction')
OPS={'register_model':1,'begin_step':2,'evaluate':3,'end_step':4,'candidate_id':5,'get_result':6,'solver_options':8,'profile':9}
OUTPUT_FIELDS=('radiant_c', 'longwave_to_network_w', 'zone_c', 'hvac_w', 'electricity_w', 'convection_to_air_w', 'ventilation_to_air_w', 'hvac_to_air_w', 'longwave_to_ground_w', 'contact_to_ground_w', 'longwave_to_sky_w', 'storage_w', 'balance_residual_w')
SURFACE_FIELDS=('structure_into_interior_w', 'exterior_convection_to_air_w', 'interior_convection_to_zone_w', 'interior_longwave_to_surface_w', 'transmitted_solar_deposited_w', 'exterior_contact_to_ground_w', 'external_net_lw_w', 'body_balance_residual_w', 'exterior_c', 'interior_c', 'storage_w', 'storage_delta_kwh', 'incident_w', 'reflected_w', 'absorbed_w', 'transmitted_w')

def array(values):
    a=np.asarray(values,dtype='<f8').ravel()
    return struct.pack('<I',len(a))+a.tobytes()

def frame(op,payload,request_id=1):
    if len(payload)>MAX_FRAME:raise ValueError('Frame exceeds 256 MiB; split the batch')
    return HEADER.pack(MAGIC,1,op,request_id,len(payload))+payload

class Reader:
    def __init__(self,data):self.data=memoryview(data);self.pos=0
    def unpack(self,fmt):
        s=struct.Struct('<'+fmt);v=s.unpack_from(self.data,self.pos);self.pos+=s.size
        return v[0] if len(v)==1 else v
    def floats(self,n):
        a=np.frombuffer(self.data,dtype='<f8',count=n,offset=self.pos);self.pos+=8*n
        return a.tolist()
    def array(self):return self.floats(self.unpack('I'))
    def finish(self):
        if self.pos!=len(self.data):raise ValueError('Trailing binary payload')

def text(value):
    raw=value.encode('utf-8');return struct.pack('<I',len(raw))+raw

def encode_hour(h):
    reject_unknown(h,FIELDS,'hour')
    return struct.pack('<9d',*(h.get(k,0.) for k in SCALARS))+b''.join(array(h.get(k,[])) for k in ARRAYS)

def reject_unknown(mapping,allowed,label):
    extra=set(mapping)-set(allowed)
    if extra:raise ValueError(f'Unknown {label} fields: {sorted(extra)}')

def encode_models(models):
    pieces=[struct.pack('<I',len(models))]
    for m in models:
        reject_unknown(m,('protocol','surfaces','hours','initial_state')+MODEL_SCALARS,'model')
        pieces.extend((text(m['protocol']),struct.pack('<6d',*(m.get(k,0.) for k in MODEL_SCALARS)),struct.pack('<I',len(m['surfaces']))))
        for s in m['surfaces']:
            reject_unknown(s,('name','kind','layers')+MODEL_SURFACE_FIELDS,'surface')
            for layer in s.get('layers',[]):reject_unknown(layer,('resistance_m2k_w','capacity_j_m2k'),'layer')
            pieces.extend((text(s['name']),text(s['kind']),struct.pack('<%dd'%len(MODEL_SURFACE_FIELDS),*(s.get(k,0.) for k in MODEL_SURFACE_FIELDS)),struct.pack('<I',len(s.get('layers',[])))))
            pieces.extend(struct.pack('<2d',l['resistance_m2k_w'],l['capacity_j_m2k']) for l in s.get('layers',[]))
        pieces.append(struct.pack('<I',len(m['hours'])))
        pieces.extend(encode_hour(h) for h in m['hours'])
        pieces.append(array(m.get('initial_state',[])))
    return b''.join(pieces)

def encode_step(batch,step_id):
    pieces=[struct.pack('<QI',step_id,len(batch))]
    for p in batch:
        if len(p['hours'])!=1:raise ValueError('A cached step requires exactly one hour')
        h=p['hours'][0]
        pieces.extend((array(p.get('initial_state',[])),encode_hour(h)))
    return b''.join(pieces)

def encode_evaluate(hours,step_id,response_level='totals',return_state=False,aggregation='batch',dynamic_fields=('air_c','ground_c','network_ir_w_m2')):
    if aggregation not in ('batch','building'):raise ValueError('Unknown aggregation')
    if response_level not in LEVELS:raise ValueError('Unknown response level')
    if len(set(dynamic_fields))!=len(dynamic_fields) or set(dynamic_fields)-set(FIELDS):raise ValueError('Unknown or duplicate dynamic fields')
    mask=sum(1<<FIELDS.index(k) for k in dynamic_fields)
    pieces=[struct.pack('<QIIII',step_id,LEVELS[response_level],int(bool(return_state)),int(aggregation=='building'),mask)]
    for h in hours:
        pieces.extend(struct.pack('<d',h.get(k,0.) if k.startswith('additional_') else h[k]) for k in SCALARS if k in dynamic_fields)
        pieces.extend(array(h.get(k,[])) for k in ARRAYS if k in dynamic_fields)
    return b''.join(pieces)

def decode_evaluate(raw,models,response_level='totals',return_state=False,aggregation='batch'):
    r=Reader(raw);n=r.unpack('I')
    if n!=len(models):raise ValueError('Response model count differs')
    air,ground,res=r.floats(3);out={'air_w':air,'ground_w':ground,'max_abs_residual_w':res}
    if aggregation=='building':out['building_totals']=np.asarray(r.floats(2*n)).reshape(n,2).tolist()
    batch=[];temperatures=[];states=[]
    for m in models:
        if response_level=='coupling':
            count=r.unpack('I')
            if count!=len(m['surfaces']):raise ValueError('Surface count differs')
            temperatures.append(dict(zip((s['name'] for s in m['surfaces']),r.floats(count))))
        elif response_level=='full':
            h=dict(zip(OUTPUT_FIELDS,r.floats(len(OUTPUT_FIELDS))))
            count=r.unpack('I')
            if count!=len(m['surfaces']):raise ValueError('Surface count differs')
            h['surfaces']=[{'name':s['name'],**dict(zip(SURFACE_FIELDS,r.floats(len(SURFACE_FIELDS))))} for s in m['surfaces']]
            batch.append({'protocol':m['protocol'],'hours':[h]})
        if return_state:
            state=r.array();states.append(state)
            if response_level=='full':batch[-1]['final_state']=state
    if response_level=='full':out['batch']=batch
    if response_level=='coupling':out['surface_temperatures']=temperatures
    if return_state:out['final_states']=states
    r.finish();return out

def encode_request(request):
    """Independent JSON-facing adapter. Return (opcode, binary payload)."""
    op=OPS[request['op']]
    if op==1:return op,encode_models(request['models'])
    if op==2:return op,encode_step(request['batch'],request['step_id'])
    if op==3:return op,encode_evaluate(request['hours'],request['step_id'],request.get('response_level','totals'),request.get('return_state',False),request.get('aggregation','batch'),request.get('dynamic_fields',FIELDS))
    if op in (4,5):return op,struct.pack('<Q',request['step_id'])
    if op==6:
        aggregation=request.get('aggregation','batch')
        if aggregation not in ('batch','building'):raise ValueError('Unknown aggregation')
        return op,struct.pack('<QQIII',request['step_id'],request['candidate_id'],LEVELS[request.get('response_level','full')],int(request.get('return_state',True)),int(aggregation=='building'))
    if op==8:return op,struct.pack('<II',int(request.get('warm_start',True)),int(request.get('candidate_cache',True)))
    if op==9:return op,b''
    raise ValueError('Unknown operation')

def convert_cli():
    p=argparse.ArgumentParser(description='Convert CES3 request JSON to binary frames or decode an evaluate response')
    p.add_argument('mode',choices=['encode','decode']);p.add_argument('input');p.add_argument('output');p.add_argument('--models');p.add_argument('--level',choices=LEVELS,default='totals');p.add_argument('--state',action='store_true');p.add_argument('--aggregation',choices=['batch','building'],default='batch')
    a=p.parse_args()
    if a.mode=='encode':
        req=json.loads(Path(a.input).read_text());requests=req if isinstance(req,list) else [req]
        data=b''.join(frame(*encode_request(q),request_id=i+1) for i,q in enumerate(requests));Path(a.output).write_bytes(data)
    else:
        if not a.models:p.error('--models is required for response layout')
        data=Path(a.input).read_bytes();pos=0;results=[];models=json.loads(Path(a.models).read_text())
        while pos<len(data):
            magic,version,op,rid,n=HEADER.unpack_from(data,pos);pos+=HEADER.size
            if magic!=MAGIC or version!=1 or n>MAX_FRAME or pos+n>len(data):raise ValueError('Invalid/truncated frame')
            payload=data[pos:pos+n];pos+=n
            if op==65535:results.append({'request_id':rid,'error':payload.decode()})
            elif op in (3,6):results.append({'request_id':rid,**decode_evaluate(payload,models,a.level,a.state,a.aggregation)})
            elif op==5:results.append({'request_id':rid,'candidate_id':struct.unpack('<Q',payload)[0]})
            elif op==9:
                keys=('evaluate_requests','get_result_requests','building_solves','building_cache_hits','diagnostic_rebuilds','nonlinear_iterations','warm_starts','cold_fallbacks','decode_validate_seconds','parallel_solve_seconds','result_format_seconds','frame_write_seconds')
                results.append({'request_id':rid,'profile':dict(zip(keys,struct.unpack('<8Q4d',payload)))})
            else:results.append({'request_id':rid,'op':op,'ack':True})
        Path(a.output).write_text(json.dumps(results,ensure_ascii=False,allow_nan=False,indent=2))
if __name__=='__main__':convert_cli()
