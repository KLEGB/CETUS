"""Transport equivalence, cached-state immutability and codec/file coverage."""
import copy,json,struct,subprocess,tempfile,unittest
from pathlib import Path
import numpy as np
from ENERGY.surface import SurfaceEngine,build_surface_input,ensure_surface_binary
from ENERGY.surface_binary import BinarySurfaceEngine
from ENERGY.surface_codec import FIELDS,HEADER,encode_request,frame,decode_evaluate
from test.test_coupled import small

class BinaryTests(unittest.TestCase):
    def models(self):
        n=small();p,_=build_surface_input(n.buildings[0],n.climate[0]);p['surfaces'][0]['layers']=[{'resistance_m2k_w':.3,'capacity_j_m2k':0},{'resistance_m2k_w':1.2,'capacity_j_m2k':70000}];p['surfaces'][0]['thermal_mass_j_m2k']=0
        q=copy.deepcopy(p);q['surfaces']=[s for s in q['surfaces'] if s['kind']!='window']
        for h in q['hours']:
            # remove the same per-surface entries
            indices=[i for i,s in enumerate(p['surfaces']) if s['kind']!='window']
            for key in ('irradiance_w_m2','ho_w_m2k'):h[key]=[h[key][i] for i in indices]
        return [p,q]
    def test_levels_dynamic_fields_and_immutable_state(self):
        models=self.models();batch=[{'hours':[copy.deepcopy(p['hours'][12])]} for p in models];hours=[b['hours'][0] for b in batch]
        with SurfaceEngine() as j,BinarySurfaceEngine(warm_start=False) as b:
            j.configure(models);b.configure(models);b.begin_step(batch)
            baseline=j.request(batch,use_models=True)
            got=b.evaluate(hours,response_level='full',return_state=True)
            self.assertEqual(baseline,got['batch'])
            light=b.evaluate(hours,response_level='coupling',aggregation='building',return_state=True)
            totals=b.evaluate(hours)
            self.assertEqual(light['final_states'],[p['final_state'] for p in baseline])
            for i,o in enumerate(baseline):
                self.assertEqual(light['surface_temperatures'][i],{s['name']:s['exterior_c'] for s in o['hours'][0]['surfaces']})
            self.assertEqual({k:totals[k] for k in ('air_w','ground_w')},j.trial(batch))
            # Deliberately change every supported dynamic field, then repeat baseline.
            modified=copy.deepcopy(hours)
            for h in modified:
                h.update(air_c=35.,ground_c=38.,sky_ir_w_m2=450.,internal_w=800.,heat_setpoint_c=18.,cool_setpoint_c=24.,ventilation_w_k=100.,additional_transmitted_w=50.,additional_glass_inward_w=20.)
                h['ho_w_m2k']=[0. if v==0 else 15. for v in h['ho_w_m2k']]
                h['irradiance_w_m2']=[v*1.2 for v in h['irradiance_w_m2']]
                h['network_ir_w_m2']=[430.]*len(h['irradiance_w_m2'])
            modified[1]['additional_glass_inward_w']=0.
            mb=[{'hours':[h]} for h in modified]
            self.assertEqual(j.request(mb,use_models=True),b.evaluate(modified,response_level='full',return_state=True)['batch'])
            self.assertEqual(got,b.evaluate(hours,response_level='full',return_state=True))
            b.end_step()
            with self.assertRaises(ValueError):b.evaluate(hours)
    def test_validation_error_and_recovery(self):
        models=self.models();batch=[{'hours':[p['hours'][0]]} for p in models]
        with BinarySurfaceEngine(warm_start=False) as b:
            b.configure(models);b.begin_step(batch)
            with self.assertRaises(ValueError):b.evaluate([{'air_c':float('nan')}] * 2,dynamic_fields=('air_c',))
            with self.assertRaises(ValueError):b._send(3,struct.pack('<QIIII',999,0,0,0,0))
            with self.assertRaises(ValueError):b.begin_step([{'hours':[p['hours'][0]],'initial_state':[20.]} for p in models])
            b.begin_step(batch);self.assertTrue(np.isfinite(b.evaluate([p['hours'][0] for p in models])['air_w']))
            b.configure(models)
            with self.assertRaises(ValueError):b.evaluate([p['hours'][0] for p in models])
    def test_binary_files_and_json_adapter(self):
        models=self.models();batch=[{'hours':[p['hours'][0]]} for p in models]
        requests=[{'op':'register_model','models':models},{'op':'begin_step','step_id':1,'batch':batch},{'op':'evaluate','step_id':1,'hours':[p['hours'][0] for p in models],'response_level':'full','return_state':True}]
        raw=b''.join(frame(*encode_request(q),request_id=i+1) for i,q in enumerate(requests))
        run=subprocess.run([str(ensure_surface_binary()),'--binary'],input=raw,capture_output=True,check=True)
        pos=0;answers=[]
        while pos<len(run.stdout):
            magic,v,op,rid,n=HEADER.unpack_from(run.stdout,pos);pos+=HEADER.size;data=run.stdout[pos:pos+n];pos+=n
            self.assertNotEqual(op,65535,data)
            if op==3:answers.append(decode_evaluate(data,models,'full',True))
        with BinarySurfaceEngine(warm_start=False) as b:
            b.configure(models);b.begin_step(batch)
            self.assertEqual(answers[0],b.evaluate([p['hours'][0] for p in models],response_level='full',return_state=True))
    def test_binary_json_coupled_equivalence(self):
        from ENERGY.coupled import simulate_coupled
        a=simulate_coupled(small(),preheat=0,show_progress=False,engine_protocol='json',longwave_mode='direct',energy_warm_start=False)
        b=simulate_coupled(small(),preheat=0,show_progress=False,engine_protocol='binary',longwave_mode='direct',energy_warm_start=False)
        self.assertEqual(a['hourly'],b['hourly']);self.assertEqual(a['coupled']['final_state'],b['coupled']['final_state'])
