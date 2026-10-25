"""Self-contained tests: python -m unittest discover -s test -t . -v"""
import sys,unittest,math
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ENERGY.daylight import (SpectralFractions,SpectralWindow,DaylightConfig,
                             estimate_daylighting,floor_areas,model_from_building)

class DaylightTests(unittest.TestCase):
    def glass(self):return SpectralWindow(.7,.2,.0,SpectralFractions(.43,.52,.05))

    def run_case(self, facade=(0,200,200),roof=(0,0,0),base=(8,8,8),**kw):
        opts=dict(facade_shortwave_w_m2=facade,roof_shortwave_w_m2=roof,
            baseline_lighting_w_m2=base,typical_floor_area_m2=100,
            gross_floor_area_m2=100,perimeter_zone_area_m2=60,
            facade_window_area_m2=20, glazing=self.glass(),
            config=DaylightConfig(threshold_lux=300, facade_utilization=.22))
        opts.update(kw)
        return estimate_daylighting(**opts)

    def test_spectral_conserves_broadband_thermal_transmittance(self):
        g=self.glass()
        self.assertAlmostEqual(g.broadband_solar_transmittance,.43*.7+.52*.2,12)
        with self.assertRaises(ValueError):
            SpectralWindow(.8,.5, fractions=SpectralFractions(.5,.3,.3))

    def test_illuminance_units(self):
        r=self.run_case()
        self.assertAlmostEqual(r.facade_illuminance_lux[1],200*.43*250,places=7)
        E=200*.43*250*.7*20*.22/60
        self.assertAlmostEqual(r.illuminance_lux_by_group['floor_1:perimeter'][1],E)
        self.assertEqual(r.illuminance_lux_by_group['floor_1:core'][1],0)

    def test_equal_solar_transmittance_selective_glass(self):
        selective=self.glass()
        t=selective.broadband_solar_transmittance/.95
        normal=SpectralWindow(t,t,0.)
        self.assertAlmostEqual(normal.broadband_solar_transmittance,
                               selective.broadband_solar_transmittance,14)
        a=self.run_case(facade=(0,70,200),glazing=normal)
        b=self.run_case(facade=(0,70,200),glazing=selective)
        key='floor_1:perimeter'
        np.testing.assert_allclose(b.illuminance_lux_by_group[key],
            a.illuminance_lux_by_group[key]*(.7/t))
        self.assertGreater(a.lighting_w[1],b.lighting_w[1])
        self.assertTrue(np.all(b.lighting_w<=a.lighting_w))

    def test_on_off_threshold_and_unlit_core(self):
        r=self.run_case(facade=[0,200,600])
        # 100 m2, 8 W/m2 baseline: 800 W each hour
        # At 200 W/m2, perimeter lux is ~1103 (>300), so only core 40% is on
        np.testing.assert_allclose(r.baseline_lighting_w,800)
        np.testing.assert_allclose(r.lighting_w,[800,320,320])
        self.assertAlmostEqual(sum(r.group_areas_m2.values()),100)

    def test_schedule_zero_wins_and_no_window_fallback(self):
        r=self.run_case(base=(0,8,8),facade_window_area_m2=0)
        np.testing.assert_allclose(r.lighting_w,[0,800,800]);self.assertGreater(len(r.notes),0)

    def test_partial_floor_and_area_partition(self):
        self.assertEqual(floor_areas(100,250),[100,100,50])
        r=self.run_case(gross_floor_area_m2=250,facade_window_area_m2=50,
                        facade=[0,0,0])
        self.assertAlmostEqual(sum(r.group_areas_m2.values()),250)
        np.testing.assert_allclose(r.lighting_w,[2000,2000,2000])

    def test_floor_rooms_windowed_and_windowless(self):
        r=self.run_case(rooms_by_floor={0:[
            {'name':'A','area_m2':50,'has_window':True,'window_area_m2':20},
            {'name':'B','area_m2':50,'has_window':False}]})
        # Room B is never artificially classified as daylit.
        self.assertIn('floor_1:B:core',r.group_areas_m2)
        self.assertNotIn('floor_1:B:perimeter',r.group_areas_m2)
        self.assertEqual(r.illuminance_lux_by_group['floor_1:B:core'][1],0)
        self.assertAlmostEqual(sum(r.group_areas_m2.values()),100)

    def test_incomplete_room_labels_conservative_fallback(self):
        r=self.run_case(rooms_by_floor={0:[{'area_m2':40},{'area_m2':60}]})
        self.assertIn('floor_1:perimeter',r.group_areas_m2)
        self.assertTrue(any('lacked window access' in x for x in r.notes))

    def test_partial_control_and_roof_daylight(self):
        r=self.run_case(facade=(0,0,0),roof=(0,100,100),
            skylight_area_m2=25,config=DaylightConfig(controlled_fraction=.5,
                threshold_lux=300,skylight_utilization=.35))
        self.assertEqual(r.lighting_w[0],800)
        self.assertGreaterEqual(r.lighting_w[1],400)
        self.assertLessEqual(r.lighting_w[1],800)

    def test_reflection_is_additive_and_spectrum_adjustable(self):
        r=self.run_case(facade=(0,0,0),facade_reflected_w_m2=(0,100,0),
             config=DaylightConfig(visible_reflected_fraction=.5))
        self.assertAlmostEqual(r.visible_facade_irradiance_w_m2[1],50)
        self.assertAlmostEqual(r.near_infrared_facade_irradiance_w_m2[1],100*.5*.52/(.52+.05))

    def test_building_bridge(self):
        class B:
            FloorArea=100.; GFA=200.; PerimeterZoneArea=60.;
            ExteriorWindowArea=20.; SkylightArea=0.
            WindowVisibleTransmittance=.7; WindowNIRTransmittance=.2
        r=model_from_building(B(),None,{'facade':[0,200,300],
             'roof':[0,0,0]},[8,8,8],{'enabled':True})
        self.assertAlmostEqual(sum(r.group_areas_m2.values()),200)
        self.assertEqual(r.baseline_lighting_w[0],1600)

    def test_skylight_only_room_assignment(self):
        r=self.run_case(facade=(0,0,0),roof=(0,500,500),facade_window_area_m2=0,
            skylight_area_m2=20,rooms_by_floor={0:[
                {'name':'toplit','area_m2':50,'skylight_area_m2':20},
                {'name':'enclosed','area_m2':50,'skylight_area_m2':0}]})
        self.assertEqual(r.illuminance_lux_by_group['floor_1:enclosed:core'][1],0)
        self.assertGreater(r.illuminance_lux_by_group['floor_1:toplit:core'][1],300)

    def test_duplicate_room_names_rejected(self):
        with self.assertRaises(ValueError):
            self.run_case(rooms_by_floor={0:[
                {'name':'duplicate','area_m2':50,'has_window':False},
                {'name':'duplicate','area_m2':50,'has_window':False}]})

    def test_bad_inputs_fail_not_silent(self):
        with self.assertRaises(ValueError):self.run_case(perimeter_zone_area_m2=110)
        with self.assertRaises(ValueError):self.run_case(facade=(-1,1,1))
        with self.assertRaises(ValueError):self.run_case(rooms_by_floor={0:[{'area_m2':200,'has_window':True}]})
        with self.assertRaises(ValueError):self.run_case(config=DaylightConfig(threshold_lux=0))

if __name__=='__main__':unittest.main()
