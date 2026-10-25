import copy,json,sys,unittest
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from scipy.integrate import quad
from scipy.sparse import csr_matrix
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from UHI.spectral import *
from RAD.spectral import SpectralNetwork
from ENERGY.surface import build_surface_input,SurfaceEngine
from ENERGY.surface_binary import BinarySurfaceEngine
from test_coupled import small

def config(e=None):
 c=dict(enabled=True,bands=[dict(name='below_8',lower_um=0,upper_um=8,atmospheric_transmittance=.02),
 dict(name='window_8_13',lower_um=8,upper_um=13,atmospheric_transmittance=.75),
 dict(name='above_13',lower_um=13,upper_um=None,atmospheric_transmittance=.02)],materials={})
 if e is not None:c['materials']['roof']={'emissivity':dict(zip(['below_8','window_8_13','above_13'],e))}
 return c

def transport(c):
 net=SimpleNamespace(meta={},group_area=np.ones(3)*100,group_sky=np.array([.6,.8,.6]),
 view_sum=np.array([.3,.2,.3]),far=np.array([.1,0,.1]),
 group_exchange=csr_matrix([[0,.1,.2],[.1,0,.1],[.2,.1,0]]))
 models=[{'surfaces':[dict(name=k,kind=k,area_m2=100,emissivity=.8) for k in ['wall','roof']]}]
 return SpectralNetwork(net,models,validate_config(c),[.8,.8,.8],[.6,.3,.1]),models

class SpectralTests(unittest.TestCase):
 def test_planck_independent_quadrature_and_partition(self):
  bands=config()['bands']
  for t in [230.,273.15,300.,320.078,370.]:
   expected=SIGMA*t**4*15/np.pi**4*quad(lambda x:x**3/np.expm1(x),C2/(13*t),C2/(8*t))[0]
   self.assertAlmostEqual(float(exact_band_exitance(t,8,13)),expected,places=8)
   self.assertLess(abs(blackbody(t,bands).sum()-SIGMA*t**4),.0001)
 def test_integral_is_weighted_and_less_than_one(self):
  c=validate_config(config([.1,.95,.1]));e,r,t=resolve_material(c['materials']['roof'],.9,c['bands'])
  self.assertGreater(sum(e),1);self.assertLess(float(e@blackbody(300,c['bands'])/(SIGMA*300**4)),1)
  with self.assertRaises(ValueError):resolve_material({'emissivity':dict.fromkeys([b['name'] for b in c['bands']],1.)},.9,c['bands'])
 def test_reflectance_fallback_mean_and_closure(self):
  bands=config()['bands'];spec={'emissivity':dict.fromkeys([b['name'] for b in bands],.8),'reflectance':{'below_8':.2,'above_13':.2}}
  e,r,t=resolve_material(spec,.9,bands);np.testing.assert_allclose(r,.2);np.testing.assert_allclose(t,0,atol=1e-15)
  spec['reflectance']['above_13']=.4
  with self.assertRaises(ValueError):resolve_material(spec,.9,bands)
 def test_no_overlap_gaps_invalid_atmosphere(self):
  for v in [7,9]:
   c=config();c['bands'][1]['lower_um']=v
   with self.assertRaises(ValueError):validate_config(c)
  c=config();c['bands'][1]['atmospheric_transmittance']=1.1
  with self.assertRaises(ValueError):validate_config(c)
 def test_one_reflection_matches_matrix_identity(self):
  s,m=transport(config([.1,.95,.1]));temps=[{'wall':30.,'roof':30.}]
  ir,gl,j,inc=s.response(temps,25.,300.).boundary([(.6,25.),(.3,25.),(.1,25.)],25.)
  x=s.last;direct=s.net.group_exchange@x['emission']+x['environment']
  expected=direct+s.net.group_exchange@(s.group_rho*direct)
  np.testing.assert_allclose(x['incident'],expected,atol=1e-12)
  actual=float(np.sum(s.areas[:,None]*(s.eps*blackbody(s.temperature_array(temps)+273.15,s.bands)-s.eps*x['incident'][s.groups])))
  audit=s.audit(temps,None,25.,actual)
  self.assertLess(abs(audit['lw_spectral_transport_residual_w']),1e-8)
  self.assertGreater(audit['lw_second_reflection_unresolved_w'],0.)
 def test_atmosphere_clear_opaque_and_epw_matching(self):
  c=config();bb=blackbody(300.,c['bands'])
  clear,_,_=atmosphere(c,26.85,300.)
  self.assertLess(clear[1]/bb[1],clear[0]/bb[0])
  c['sky_mode']='epw_matched';sky,_,_=atmosphere(c,26.85,350.)
  self.assertAlmostEqual(float(sky.sum()),350.,places=7)
 def test_json_binary_spectral_and_legacy_interior_preserved(self):
  n=small();p,_=build_surface_input(n.buildings[0],n.climate[0],options={'longwave':config([.1,.95,.1])})
  with SurfaceEngine() as eng:a=eng.request([p])[0]
  with BinarySurfaceEngine() as eng:
   eng.configure([p]);h=copy.deepcopy(p);h['hours']=[p['hours'][0]]
   eng.begin_step([h]);b=eng.evaluate(h['hours'],response_level='full',return_state=True,dynamic_fields=())['batch'][0]
  # The comparison must use the same one-hour initial state, not the 24h final state.
  one=copy.deepcopy(p);one['hours']=one['hours'][:1]
  with SurfaceEngine() as eng:a=eng.request([one])[0]
  np.testing.assert_allclose(a['final_state'],b['final_state'],atol=1e-9)
  self.assertLess(abs(a['hours'][0]['balance_residual_w']),1e-5)
  self.assertEqual(p['surfaces'][1]['emissivity'],.9)
 def test_window_profile_changes_thermal_result(self):
  n=small();outs=[]
  for e in [[.4,.4,.4],[.1,.95,.1]]:
   p,_=build_surface_input(n.buildings[0],n.climate[0],options={'longwave':config(e)})
   with SurfaceEngine() as eng:outs.append(eng.request([p])[0])
  ta=[x['hours'][12]['surfaces'][1]['exterior_c'] for x in outs]
  self.assertGreater(abs(ta[0]-ta[1]),.01)

 def test_full_reflection_isothermal_equilibrium(self):
  c=config([.1,.95,.1]);c['reflection_mode']='radiosity'
  for b in c['bands']:b['atmospheric_transmittance']=0.
  s,_=transport(c);temps=[{'wall':26.85,'roof':26.85}]
  ir,ground,j,inc=s.response(temps,26.85,400.).boundary([(.6,26.85),(.3,26.85),(.1,26.85)],26.85)
  np.testing.assert_allclose(s.last['incident'],np.tile(blackbody(300.,c['bands']),(3,1)),atol=1e-9)
  np.testing.assert_allclose(ground,0.,atol=1e-9)
  audit=s.audit(temps,None,26.85,0.)
  self.assertLess(abs(audit['lw_spectral_transport_residual_w']),1e-8)
  self.assertEqual(audit['lw_second_reflection_unresolved_w'],0.)

 def test_clear_window_advantage_at_equal_integrated_emissivity(self):
  c=config();b=exact_band_exitance(300,8,13);total=SIGMA*300**4
  e=np.array([.1,.95,.1]);bb=np.array([float(exact_band_exitance(300,x['lower_um'],x['upper_um'])) for x in c['bands']])
  mean=float(e@bb/bb.sum());tau=np.array([.02,.75,.02])
  self.assertAlmostEqual(float(e@bb),mean*bb.sum(),places=9)
  self.assertGreater(float(e@(tau*bb)),float(mean*sum(tau*bb)))
