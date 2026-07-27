from __future__ import annotations

import numpy as np
from .site import SiteInfo, ClimateInfo
from .aT import AvgHeatTransfer

def shortWaveRadDifference(t, climate: ClimateInfo, site: SiteInfo, a=0.22, k=0.1):
    """
    Calculates the average Solar radiation absorption coefficient
    :param climate: climate info object
    :param site: site info object
    :param a: roughness of the ground, 0.12 for sea, 0.16 for subUrban, 0.22 for Urban,0.30 for Central City (default: 0.22)
    :param k: building ventilation overhead ratio in the design scope (default: 0.1)
    :param t: current time

    :return: Average Solar radiation absorption coefficient
    """
    CTTCD = 8  # thermal time coefficient for ground
    CTTCB = 6  # thermal time coefficient for Building
    CTTCQ = 12  # thermal time coefficient for tree
    CTTC = ((1 - (site.AreaBuilding + site.AreaTreeShading) / site.TotalArea) * CTTCD + site.AreaFacade / site.TotalArea
            * CTTCB + site.AreaTreeShading / site.TotalArea * CTTCQ)  # thermal time coefficient
    CumulatedHeat = 0
    for i in range(t):
        absorption = AvgSolarAbsorpt(site.AreaRoad, site.AreaGround, site.AreaLandscape, site.AreaWater, site.AbsoRoad,
                                     site.AbsoGround, site.AbsoLandscape, site.AbsoWater) / AvgHeatTransfer(
            climate.Velocity[t], site, a, k)
        expCTTC = 1 - np.exp((i - t) / CTTC)
        next = SolarRadiationTime(climate.RadTotal[i + 1], climate.RadDiffuse[i + 1], site.SVF, site.SDL, site.SDG,
                                   site.SRTL,
                                   site.SRTG, site.fPSA, site.CL, site.CG)
        last = SolarRadiationTime(climate.RadTotal[i], climate.RadDiffuse[i], site.SVF, site.SDL, site.SDG,
                                   site.SRTL,
                                   site.SRTG, site.fPSA, site.CL, site.CG)
        radi = (next - last)
        CumulatedHeat += absorption * expCTTC * radi
    return CumulatedHeat


def SolarRadiationTime(RadTotal, RadDiffuse, SVF, SDL, SDG=0.1, SRTL=0.4, SRTG=0.2, fPSA=0.2, CL=0.7, CG=0.8):
    """

    :param RadTotal: Total radiation in time t
    :param RadDiffuse: diffuse radiation in time t
    :param SVF: Average Sky View Factor in the area
    :param SDL: Shading percentage of lanscape
    :param SDG: Shading percentage of urban construction (default=0.1)
    :param SRTL: Transparency of lanscape (default: 0.4)
    :param SRTG: Transparency of urban construction (default: 0.2)
    :param fPSA: Shading percentage of building on the open space (default: 0.2)
    :param CL: Heat transfer coefficient of lanscape (default: 0.7)
    :param CG: Heat transfer coefficient of urban construction (default: 0.8)
    :return: solarRadiation: Adjusted solar radiation in time t
    """
    return (((RadTotal - RadDiffuse) * (1 - fPSA) + RadDiffuse * SVF)
            * (1 - SDL * (1 - SRTL) * (1 - CL))
            * (1 - SDG * (1 - SRTG) * (1 - CG))
            )


def AvgSolarAbsorpt(AreaRoad, AreaGround, AreaLandscape, AreaWater, AbsoRoad=0.74, AbsoGround=0.74, AbsoLandscape=0.80,
                    AbsoWater=0.96):
    """
    Calculates the average Solar radiation absorption coefficient
    :param AreaRoad: area of road
    :param AreaGround: area of ground
    :param AreaLandscape: area of landscape
    :param AreaWater: area of water
    :param AbsoRoad: absorption coefficient of road (default=0.74, concrete)
    :param AbsoGround: absorption coefficient of ground (default=0.74, concrete)
    :param AbsoLandscape: absorption coefficient of landscape (default=0.80, grass)
    :param AbsoWater: absorption coefficient of water (default=0.96)
    :return: Average Solar radiation absorption coefficient
    """
    return (AreaRoad * AbsoRoad + AreaGround * AbsoGround + AreaWater * AbsoWater +
            AreaLandscape * AbsoLandscape) / (AreaRoad + AreaWater + AreaGround + AreaLandscape)



