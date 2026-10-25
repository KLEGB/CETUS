import sys,copy,tempfile,json,subprocess,contextlib,io
from pathlib import Path
from unittest.mock import patch
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test.test_coupled import small
from ENERGY import Neighborhood
from ENERGY.coupled import simulate_coupled
from ENERGY.surface import ensure_surface_binary
from ENERGY.result_store import read_results,export_json,ResultWriter,encode,decode
class ArchiveTests(unittest.TestCase):
 def test_all_buildings_snapshot_and_city_retained(self):
  n=small();b=copy.deepcopy(n.buildings[0]);b.building_id='second';n.buildings.append(b)
  with tempfile.TemporaryDirectory() as td:
   r=simulate_coupled(n,preheat=0,show_progress=False,output_dir=td)
   before=copy.deepcopy(n.toDict());a=read_results(r['building_result_file'])
   self.assertEqual(len(a['buildings']),2)
   self.assertTrue(all(len(x['hours'])==24 for x in a['buildings']))
   self.assertEqual(len(a['group_totals']),24)
   for i,x in enumerate(a['buildings']):
    self.assertEqual(x['parameters'],before['buildings'][i]);self.assertEqual(len(x['applied_inputs']),24)
    self.assertTrue(x['resolved_model']['surfaces']);self.assertTrue(x['hours'][0]['final_state'])
   self.assertTrue(all(not b.energyFeedback and b.ACLoad.size==0 for b in n.buildings))
   self.assertNotIn('energyFeedback',before['buildings'][0]);self.assertNotIn('_energy_results',before)
   self.assertTrue(any(abs(v)>1 for v in n.climate[0].ACWasteHeat))
   restored=Neighborhood.fromDict(before)
   self.assertEqual(restored.toDict(),before)
   n.buildings[0].WallUValue=99
   self.assertNotEqual(read_results(r['building_result_file'])['buildings'][0]['parameters']['WallUValue'],99)
   p=Path(td)/'all.json'
   # Pure-stdlib independent application: no installed site packages required.
   subprocess.run([sys.executable,'-S',str(Path(__file__).resolve().parents[1]/'ENERGY'/'unpack.py'),r['building_result_file'],str(p)],check=True,capture_output=True)
   self.assertEqual(json.loads(p.read_text()),a)
 def test_raw_response_and_decoded_thermal_meter(self):
  with tempfile.TemporaryDirectory() as td:
   n=small();r=simulate_coupled(n,preheat=0,show_progress=False,output_dir=td)
   a=read_results(r['building_result_file']);rows=a['buildings'][0]['hours']
   self.assertAlmostEqual(sum(x['electricity_w'] for x in rows)/1000,r['coupled']['lighting_summary']['hvac_electricity_kwh'])
   import zipfile
   with zipfile.ZipFile(r['building_result_file']) as z:
    m=json.loads(z.read('manifest.json'));self.assertTrue(all(x['encoding']=='CES3-full-state' for x in m['records']))
   for t,x in enumerate(rows):
    self.assertAlmostEqual(x['convection_to_air_w']+x['ventilation_to_air_w']+x['hvac_to_air_w'],n.climate[0].ACWasteHeat[t],places=7)
 def test_no_sha_or_compiler_and_missing_executable(self):
  with patch('pathlib.Path.read_text',side_effect=AssertionError('must not read SHA')),patch('subprocess.run',side_effect=AssertionError('must not compile')):
   self.assertTrue(ensure_surface_binary().is_file())
  with tempfile.TemporaryDirectory() as td:
   with self.assertRaises(FileNotFoundError):ensure_surface_binary(Path(td)/'missing')
 def test_failed_production_does_not_publish_archive(self):
  n=small();original=copy.deepcopy(n.climate[0].to_dict())
  with tempfile.TemporaryDirectory() as td:
   with self.assertRaises(RuntimeError):simulate_coupled(n,preheat=0,show_progress=False,max_evaluations=1,balance_tolerance_w_m2=1e-14,output_dir=td)
   self.assertFalse((Path(td)/'building_results.bin').exists());self.assertFalse(list(Path(td).glob('*.tmp')))
   self.assertEqual(n.climate[0].to_dict(),original)
 def test_defaults_and_typed_roundtrip(self):
  import inspect
  self.assertEqual(inspect.signature(simulate_coupled).parameters['warmup_tolerance_k'].default,.1)
  self.assertEqual(inspect.signature(simulate_coupled).parameters['max_preheat'].default,120)
  from ENERGY.surface_codec import OUTPUT_FIELDS,SURFACE_FIELDS
  from ENERGY.result_store import OUTPUT,SURFACE
  self.assertEqual(OUTPUT_FIELDS,OUTPUT);self.assertEqual(SURFACE_FIELDS,SURFACE)
  x={'x':[-1,0,1.,False,None,'波段'],'y':{'key':[]}}
  self.assertEqual(decode(encode(x)),x)
  with self.assertRaises(ValueError):decode(encode(x)[:-1])

 def test_spectral_coupled_archive_includes_resolved_materials(self):
  from RAD.network import RadiationNetwork,optical_signature
  from scipy.sparse import csr_matrix
  from test.test_spectral import config
  n=small();options={'longwave':config([.1,.95,.1])}
  area=np.array([320.,100.,9900.]);C=np.array([[0,10,100],[10,0,0],[100,0,0.]])
  F=csr_matrix(C/area[:,None]);zeros=np.zeros((3,24))
  meta=dict(ground_emissivity=.95,ground_reflectance=.2,ignore_window_longwave=False,optical_signature=optical_signature(n,options),energy_options=options,longwave_updates=0)
  net=RadiationNetwork(np.zeros((3,3)),np.tile([0.,0.,1.],(3,1)),area,np.array([0,0,-1]),np.arange(3),np.array([.5,.9,.6]),F,F,zeros.copy(),zeros.copy(),np.arange(24),meta)
  with tempfile.TemporaryDirectory() as td:
   net.path=str(Path(td)/'net.npz');n._radiation_network=net
   r=simulate_coupled(n,options=options,preheat=0,show_progress=False,output_dir=td)
   a=read_results(r['building_result_file']);resolved=a['context']['resolved_spectral']
   self.assertTrue(resolved['emissivity']);self.assertTrue(resolved['reflectance'])
   self.assertEqual(len(a['buildings'][0]['hours']),24)
   self.assertIn('longwave_bands',a['buildings'][0]['resolved_model']['surfaces'][1])

 def test_warmup_limit_retains_solved_state_and_can_resume(self):
  with tempfile.TemporaryDirectory() as td:
   with self.assertWarns(RuntimeWarning):
    first=simulate_coupled(small(),preheat=1,max_preheat=3,warmup_tolerance_k=1e-12,show_progress=False,output_dir=td)
   self.assertNotIn('next_guess_accelerated',first['coupled']['warmup'][-1])
   with self.assertWarns(RuntimeWarning):
    second=simulate_coupled(small(),preheat=1,max_preheat=3,warmup_tolerance_k=1e-12,show_progress=False,warmup_checkpoint=Path(td)/'warmup_checkpoint.json',output_dir=Path(td)/'resume')
   np.testing.assert_allclose(first['Tair'],second['Tair'],atol=1e-9)
