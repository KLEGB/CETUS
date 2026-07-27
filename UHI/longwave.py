from __future__ import annotations

import numpy as np
from .modelInfo import SiteInfo, ClimateInfo, settingInfo
from .constant import StefanBoltzmann
from .aT import saturationVaporPressure



# def longWaveRadHeatIntensity(t, climate: ClimateInfo, site: SiteInfo, settings: settingInfo):
#     """
#     Main Function
#     Calculate the long wave radiation heat intensity
#     :param climate: climate info object
#     :param site: site info object
#     :param t: current time
#     :return: long wave radiation heat intensity in specific daytime t (W/m2)
#     """
#     dc = -StefanBoltzmann * ((climate.Tg[t-1] + 273) ** 4) * (1 - prandtl(climate.Rh[t-1], climate.Ta[t-1], settings.BruntEmissivity)) * site.SVF 
#     return dc

def longWaveRadHeatIntensity(t, climate: ClimateInfo, site: SiteInfo, settings: settingInfo):
    """
    Main Function
    Calculate the long wave radiation heat intensity on the GROUND in the area at time t (W/m2)
    :param climate: climate info object
    :param site: site info object
    :param t: current time
    :return: long wave radiation heat intensity on the GROUND in specific daytime t (W/m2)
    in the form of a dictionary with keys "ground", "land", and "water"
    """
    dcGround = -StefanBoltzmann * ((climate.Tg_split['ground'][t-1] + 273) ** 4) * (1 - prandtl(climate.Rh[t-1], climate.Ta_split['ground'][t-1], settings.BruntEmissivity)) * site.SVF 
    dcLand = -StefanBoltzmann * ((climate.Tg_split['land'][t-1] + 273) ** 4) * (1 - prandtl(climate.Rh[t-1], climate.Ta_split['land'][t-1], settings.BruntEmissivity)) * site.SVF 
    dcWater = -StefanBoltzmann * ((climate.Tg_split['water'][t-1] + 273) ** 4) * (1 - prandtl(climate.Rh[t-1], climate.Ta_split['water'][t-1], settings.BruntEmissivity)) * site.SVF 
    climate.QL['ground'][t] = dcGround
    climate.QL['land'][t] = dcLand
    climate.QL['water'][t] = dcWater
    return {"ground": dcGround, "land": dcLand, "water": dcWater}



def prandtl(RHaTMD, TaTMD, BruntEmissivity):
    """
    Brunt-type clear-sky atmospheric effective emissivity
    :parameter
        RHaTMD: Relative humidity(%) in specific daytime t
        TaTMD: Dry Bulb Temperature(C) in specific daytime t
        BruntEmissivity: Brunt-type clear-sky atmospheric effective emissivity when PaTMD=0
    :returns
        prandtl number in specific daytime t
    """
    # vapor pressure (Pa)
    # VPaTMD = RHaTMD * np.exp(23.5612 - (4030 / (TaTMD + 235)))
    VPaTMD = RHaTMD / 100 * saturationVaporPressure(TaTMD) * 1000  # convert kPa to Pa
    return BruntEmissivity + 0.1518 * np.sqrt(VPaTMD / 1000)

