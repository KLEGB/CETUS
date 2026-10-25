"""Independent nonlinear SciPy balance, assembled without Go matrix helpers."""
import unittest
import numpy as np
from scipy.optimize import root
from ENERGY.surface import SurfaceEngine, SIGMA


class IndependentSolverTest(unittest.TestCase):
    def test_node_temperatures_against_direct_residual(self):
        # Unknowns: wall external/core/internal, glass external/internal, zone, enclosure.
        surfaces=[dict(name='wall',kind='wall',area_m2=50,resistance_m2k_w=2,
            thermal_mass_j_m2k=80000,reflectance=.3,transmittance=0,emissivity=.9,
            hi_w_m2k=3,ho_w_m2k=10,sky_view=.5,ground_view=.5),
            dict(name='window',kind='window',area_m2=10,resistance_m2k_w=.3,
            thermal_mass_j_m2k=0,reflectance=.2,transmittance=.6,emissivity=.84,
            hi_w_m2k=3,ho_w_m2k=10,sky_view=.5,ground_view=.5)]
        max_error=0
        with SurfaceEngine() as engine:
            for solar in (0,100,500):
                h=dict(air_c=25,ground_c=22,sky_ir_w_m2=SIGMA*(15+273.15)**4,
                    irradiance_w_m2=[solar,solar],internal_w=100,
                    heat_setpoint_c=-50,cool_setpoint_c=150,ventilation_w_k=20)
                p=dict(protocol='cetus-surface-7',dt_s=3600,surfaces=surfaces,hours=[h],
                    initial_c=20,cooling_cop=3,heating_cop=3,internal_radiant_fraction=.6,solar_floor_fraction=0)
                go=engine.request([p])[0]
                engine.configure([p])
                cached=engine.request([{'hours':p['hours']}],use_models=True)[0]
                self.assertEqual(go,cached)
                trial=engine.trial([{'hours':p['hours']}])
                row=go['hours'][0]
                self.assertAlmostEqual(trial['air_w'],row['convection_to_air_w']+row['ventilation_to_air_w']+row['hvac_to_air_w'])
                self.assertAlmostEqual(trial['ground_w'],row['longwave_to_ground_w']+row['contact_to_ground_w'])
                def lw(a,b,eps):return eps*SIGMA*((a+273.15)**4-(b+273.15)**4)
                def residual(x):
                    we,wm,wi,ge,gi,air,mrt=x
                    transmitted=10*solar*.6
                    return np.array([
                        50*(10*(25-we)+lw(15,we,.9)*.5+lw(22,we,.9)*.5+2/2*(wm-we)+solar*.7),
                        50*2/2*(we-wm)+50*2/2*(wi-wm)-50*80000/3600*(wm-20),
                        50*(2/2*(wm-wi)+3*(air-wi)+lw(mrt,wi,.9))+transmitted*50/60,
                        10*(10*(25-ge)+lw(15,ge,.84)*.5+lw(22,ge,.84)*.5+(gi-ge)/.3+solar*.2),
                        10*((ge-gi)/.3+3*(air-gi)+lw(mrt,gi,.84))+transmitted*10/60,
                        50*3*(wi-air)+10*3*(gi-air)+20*(25-air)+40,
                        50*lw(wi,mrt,.9)+10*lw(gi,mrt,.84)+60])
                scipy=root(residual,np.full(7,25.),tol=1e-10)
                self.assertLess(np.max(np.abs(residual(scipy.x))),1e-5)
                max_error=max(max_error,float(np.max(np.abs(scipy.x-go['final_state']))))
        self.assertLess(max_error,1e-7)
        print(f'Independent SciPy maximum node difference: {max_error:.3e} K')
