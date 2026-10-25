"""CER 9.2 result archive and standalone exporter. Standard library only.
Native records are unmodified CES3 full/state binary response bodies.
No solver, NumPy, geometry or pandas is imported by this module.
"""
import json,struct,zipfile,tempfile,os,math
from pathlib import Path
OUTPUT=('radiant_c','longwave_to_network_w','zone_c','hvac_w','electricity_w','convection_to_air_w','ventilation_to_air_w','hvac_to_air_w','longwave_to_ground_w','contact_to_ground_w','longwave_to_sky_w','storage_w','balance_residual_w')
SURFACE=('structure_into_interior_w','exterior_convection_to_air_w','interior_convection_to_zone_w','interior_longwave_to_surface_w','transmitted_solar_deposited_w','exterior_contact_to_ground_w','external_net_lw_w','body_balance_residual_w','exterior_c','interior_c','storage_w','storage_delta_kwh','incident_w','reflected_w','absorbed_w','transmitted_w')
def plain(x):
 if hasattr(x,'tolist'):return plain(x.tolist())
 if isinstance(x,dict):return {str(k):plain(v) for k,v in x.items()}
 if isinstance(x,(list,tuple)):return [plain(v) for v in x]
 if isinstance(x,Path):return str(x)
 return x

def encode(x):
 x=plain(x)
 if x is None:return b'n'
 if isinstance(x,bool):return b't' if x else b'f'
 if isinstance(x,int):return b'i'+struct.pack('<q',x)
 if isinstance(x,float):return b'd'+struct.pack('<d',x)
 if isinstance(x,str):
  b=x.encode();return b's'+struct.pack('<I',len(b))+b
 if isinstance(x,list):return b'l'+struct.pack('<I',len(x))+b''.join(map(encode,x))
 if isinstance(x,dict):return b'm'+struct.pack('<I',len(x))+b''.join(encode(k)+encode(v) for k,v in x.items())
 raise TypeError(type(x))
class Reader:
 def __init__(self,data):self.data=memoryview(data);self.p=0
 def take(self,n):
  if n<0 or self.p+n>len(self.data):raise ValueError('Truncated result record')
  v=self.data[self.p:self.p+n];self.p+=n;return v
 def u(self):return struct.unpack('<I',self.take(4))[0]
 def doubles(self,n):return list(struct.unpack('<%dd'%n,self.take(n*8)))
 def array(self):return self.doubles(self.u())
 def tree(self):
  t=bytes(self.take(1))
  if t==b'n':return None
  if t==b't':return True
  if t==b'f':return False
  if t==b'i':return struct.unpack('<q',self.take(8))[0]
  if t==b'd':return self.doubles(1)[0]
  if t==b's':return bytes(self.take(self.u())).decode()
  if t==b'l':return [self.tree() for _ in range(self.u())]
  if t==b'm':
   out={}
   for _ in range(self.u()):k=self.tree();out[k]=self.tree()
   return out
  raise ValueError('Unknown result data tag')
 def finish(self):
  if self.p!=len(self.data):raise ValueError('Trailing result bytes')
def decode(data):
 r=Reader(data);v=r.tree();r.finish();return v

def native_decode(data,models):
 r=Reader(data);n=r.u()
 if n!=len(models):raise ValueError('Result/model inventory mismatch')
 totals=dict(zip(('building_to_air_w','building_to_ground_w','max_abs_residual_w'),r.doubles(3)))
 rows=[]
 for model in models:
  h=dict(zip(OUTPUT,r.doubles(len(OUTPUT))));ns=r.u()
  if ns!=len(model['surfaces']):raise ValueError('Surface inventory mismatch')
  h['surfaces']=[dict(name=s['name'],**dict(zip(SURFACE,r.doubles(len(SURFACE))))) for s in model['surfaces']]
  h['final_state']=r.array();rows.append(h)
 r.finish();return rows,totals

class ResultWriter:
 def __init__(self,path,parameters,models=None,context=None):
  self.path=Path(path).resolve();self.path.parent.mkdir(parents=True,exist_ok=True)
  fd,tmp=tempfile.mkstemp(prefix=self.path.name+'.',suffix='.tmp',dir=self.path.parent);os.close(fd);self.tmp=Path(tmp)
  self.zip=zipfile.ZipFile(self.tmp,'w',zipfile.ZIP_DEFLATED,compresslevel=3)
  self.manifest={'format':'CETUS-building-results','version':1,'implementation':'9.2.0','records':[],'complete':False,'native_schema':'CES3-v1-full-state','output_fields':OUTPUT,'surface_fields':SURFACE}
  self.zip.writestr('parameters.bin',encode(parameters));self.zip.writestr('models.bin',encode(models or []));self.zip.writestr('context.bin',encode(context or {}))
 def record(self,hoy,raw=None,rows=None,extras=None,applied_inputs=None):
  i=len(self.manifest['records']);name=f'records/{i:06d}'
  self.zip.writestr(name+'.bin',raw if raw is not None else encode(rows))
  self.zip.writestr(name+'.inputs',encode(applied_inputs))
  self.zip.writestr(name+'.extras',encode(extras or []))
  self.manifest['records'].append({'hoy':int(hoy),'path':name,'encoding':'CES3-full-state' if raw is not None else 'typed-tree'})
 def finish(self,metadata=None):
  self.zip.writestr('run_metadata.bin',encode(metadata or {}));self.manifest['complete']=True
  self.zip.writestr('manifest.json',json.dumps(self.manifest,separators=(',',':')))
  self.zip.close();self.tmp.replace(self.path);return str(self.path)
 def __enter__(self):return self
 def __exit__(self,typ,value,tb):
  if typ is not None:self.abort()
 def abort(self):
  self.zip.close();self.tmp.unlink(missing_ok=True)

def read_results(path):
 with zipfile.ZipFile(path) as z:
  m=json.loads(z.read('manifest.json'))
  if m.get('format')!='CETUS-building-results' or m.get('version')!=1 or not m.get('complete'):raise ValueError('Unsupported or incomplete result archive')
  if m.get('native_schema','CES3-v1-full-state')!='CES3-v1-full-state' or tuple(m.get('output_fields',OUTPUT))!=OUTPUT or tuple(m.get('surface_fields',SURFACE))!=SURFACE:raise ValueError('Unsupported native result schema')
  parameters=decode(z.read('parameters.bin'));models=decode(z.read('models.bin'));context=decode(z.read('context.bin'))
  buildings=[{'building_id':p.get('building_id',f'B{i:06d}'),'parameters':p,'resolved_model':models[i] if i<len(models) else None,'hours':[],'applied_inputs':[]} for i,p in enumerate(parameters)]
  group=[]
  for rec in m['records']:
   root=rec['path'];raw=z.read(root+'.bin')
   if rec['encoding']=='CES3-full-state':rows,totals=native_decode(raw,models);group.append(dict(hoy=rec['hoy'],**totals))
   elif rec['encoding']=='typed-tree':rows=decode(raw)
   else:raise ValueError('Unknown record encoding')
   if len(rows)!=len(buildings):raise ValueError('Incomplete building result inventory')
   extras=decode(z.read(root+'.extras'));inputs=decode(z.read(root+'.inputs'))
   for i,(b,row) in enumerate(zip(buildings,rows)):
    b['hours'].append(dict(row,**(extras[i] if extras else {}),hoy=rec['hoy']))
    b['applied_inputs'].append(inputs[i] if inputs else None)
  return {'implementation':'9.2.0','context':context,'buildings':buildings,'group_totals':group,'run_metadata':decode(z.read('run_metadata.bin'))}
def export_json(source,destination):
 result=read_results(source);p=Path(destination).resolve();p.parent.mkdir(parents=True,exist_ok=True)
 tmp=p.with_name(p.name+'.tmp')
 try:
  with tmp.open('w',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,separators=(',',':'),allow_nan=False)
  tmp.replace(p)
 finally:tmp.unlink(missing_ok=True)
 return p
