from __future__ import annotations

import numpy as np
from .site import SiteInfo, ClimateInfo
from .aT import AvgHeatTransfer

StefanBoltzmann = 5.67 * 10 ** -8


def longWaveRadDifference(t, climate: ClimateInfo, site: SiteInfo, a=0.22, k=0.1):
    """
    Main Function
    Calculate the temperature difference derive from long wave radiation
    :param climate: climate info object
    :param site: site info object
    :param t: current time
    :param a: roughness of the ground, 0.12 for sea, 0.16 for subUrban, 0.22 for Urban,0.30 for Central City (default: 0.22)
    :param k: building ventilation overhead ratio in the design scope (default: 0.1)
    :return: Temperature difference in specific daytime t
    """
    dc = -StefanBoltzmann * ((climate.Ta[t] + 273) ** 4) * (1 - Prandtl(climate.Rh[t], climate.Ta[t])) * site.SVF / AvgHeatTransfer(climate.Velocity[t], site, a, k)
    return dc


def Prandtl(RHaTMD, TaTMD):
    """
    :parameter
        RHaTMD: Relative humidity(%) in specific daytime t
        TaTMD: Dry Bulb Temperature(C) in specific daytime t
    :returns
        Prandtl number in specific daytime t
    """
    # vapor pressure (Pa)
    PaTMD = RHaTMD * np.exp(23.5612 - (4030 / (TaTMD + 235)))
    return 0.605 + 0.1518 * np.sqrt(PaTMD / 1000)


