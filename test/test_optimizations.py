import copy,unittest
import numpy as np
from scipy.sparse import csr_matrix
from ENERGY.surface import SurfaceEngine,build_surface_input
from ENERGY.surface_binary import BinarySurfaceEngine
from RAD.network import RadiationNetwork,LongwaveResponse
from test.test_coupled import small

class OptimizationTests(unittest.TestCase):
    def setup_inputs(self):
        n=small();model,_=build_surface_input(n.buildings[0],n.climate[0]);h=copy.deepcopy(model['hours'][12]);return model,h
    def test_candidate_upgrade_without_resolve_and_invalidation(self):
        model,h=self.setup_inputs()
        with BinarySurfaceEngine(warm_start=False) as e:
            e.configure([model]);e.begin_step([{'hours':[h]}]);e.evaluate([h]);candidate=e.last_candidate_id
            before=e.profile();a=e.get_result(candidate,response_level='full',return_state=True);after=e.profile()
            self.assertEqual(before['building_solves'],after['building_solves']);self.assertGreater(after['diagnostic_rebuilds'],before['diagnostic_rebuilds'])
            self.assertEqual(a,e.evaluate([h],response_level='full',return_state=True))
            h2={**h,'air_c':h['air_c']+2};e.evaluate([h2])
            with self.assertRaises(ValueError):e.get_result(candidate)
            candidate=e.last_candidate_id;e.end_step()
            with self.assertRaises(ValueError):e.get_result(candidate)
            e.begin_step([{'hours':[h]}]);self.assertIsNone(e.last_candidate_id)
    def test_warm_start_accuracy_and_old_state_unchanged(self):
        model,h=self.setup_inputs();batch=[{'hours':[h]}]
        with SurfaceEngine() as j,BinarySurfaceEngine(warm_start=True,candidate_cache=True) as e:
            j.configure([model]);e.configure([model]);e.begin_step(batch)
            for t in (25.,25.0001,25.0002,35.,5.,25.):
                x={**h,'air_c':t};a=j.request([{'hours':[x]}],use_models=True)[0]
                b=e.evaluate([x],response_level='full',return_state=True)['batch'][0]
                np.testing.assert_allclose(a['final_state'],b['final_state'],atol=1e-8,rtol=0)
                self.assertLess(abs(a['hours'][0]['hvac_w']-b['hours'][0]['hvac_w']),1e-5)
            self.assertGreater(e.profile()['warm_starts'],0)
            self.assertNotIn('initial_state',batch[0])
    def test_affine_longwave_and_ignored_window(self):
        model,h=self.setup_inputs();models=[model];temps=[{s['name']:20.+i for i,s in enumerate(model['surfaces'])}]
        for ignore in (False,True):
            G=csr_matrix([[0,.1,.1],[.1,0,.1],[.1,.1,0.]])
            net=RadiationNetwork(np.zeros((3,3)),np.ones((3,3)),np.full(3,100.),np.array([0,0,-1]),np.arange(3),np.full(3,.5),G,G,np.zeros((3,1)),np.zeros((3,1)),np.array([0]),{'ground_emissivity':.95,'ignore_window_longwave':ignore})
            response=LongwaveResponse(net,models,temps,400.)
            for air in (-10.,20.,45.):
                ground=[(.7,air+5),(.2,air),(.1,air-3)]
                a=net.longwave_boundary(models,temps,ground,400.,air);b=response.boundary(ground,air)
                np.testing.assert_allclose(a[0],b[0],atol=5e-12,rtol=0)
                np.testing.assert_allclose(a[1],b[1],atol=5e-12,rtol=0)
                np.testing.assert_allclose(a[2],b[2],atol=5e-12,rtol=0)
    def test_root_memoization_and_controls(self):
        from ENERGY.coupled import simulate_coupled
        a=simulate_coupled(small(),preheat=0,show_progress=False,energy_warm_start=False,candidate_cache=False,root_memoization=False,longwave_mode='direct')
        b=simulate_coupled(small(),preheat=0,show_progress=False)
        np.testing.assert_allclose(a['Tair'],b['Tair'],atol=1e-6,rtol=0)
        self.assertGreater(sum(x['trial_cache_hits'] for x in b['hourly']),0)
        self.assertGreater(b['coupled']['engine_profile']['get_result_requests'],0)
