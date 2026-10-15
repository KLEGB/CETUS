from __future__ import annotations

import numpy as np
from .modelInfo import SiteInfo, ClimateInfo, settingInfo


def shortWaveRadHeatIntensity(t, climate: ClimateInfo, site: SiteInfo, settings: settingInfo, warmupDays=1):
    """
    Calculates the average solar radiation heat intensity on the GROUND in the area at time t after warm-up (W/m2).
    The same 24-hour radiation sequence is repeated to transfer the accumulated heat from hour 23 to hour 0.

    :param t: Current hour in the typical day, ranging from 0 to 23
    :param climate: Climate info object containing at least 24-hour radiation data
    :param site: Site info object
    :param settings: Settings object carrying the solar absorption coefficients
    :param warmupDays: Number of repeated days used for CTTC warm-up (default=10)

    :return: Average solar radiation heat intensity in the form of a dictionary
             with keys "ground", "land", and "water"
    """

    CTTC = 0.5 * ((1 - (site.AreaBuilding + site.AreaTreeShading) / site.TotalArea) * settings.CTTCD
            + site.AreaFacade / site.TotalArea * settings.CTTCB
            + site.AreaTreeShading / site.TotalArea * settings.CTTCQ)

    groundArea = site.AreaRoad + site.AreaGround
    absorbGround = ((settings.AbsoRoad * site.AreaRoad + settings.AbsoGround * site.AreaGround) / groundArea
                    if groundArea > 0 else 0.0)

    CumulatedHeatGround = 0.0
    CumulatedHeatBld = 0.0
    currentStep = warmupDays * 24 + t

    for i in range(currentStep):
        expCTTC = 1 - np.exp((i - currentStep) / CTTC)
        lastHour = i % 24
        nextHour = (i + 1) % 24

        radiationNext = solarRadiationTime(
            climate.RadTotal[nextHour], climate.RadDiffuse[nextHour], site.SVF, site.SDL, site.SDG,
            site.SRTL, site.SRTG, site.fPSA, site.CL, site.CG
        )
        radiationLast = solarRadiationTime(
            climate.RadTotal[lastHour], climate.RadDiffuse[lastHour], site.SVF, site.SDL, site.SDG,
            site.SRTL, site.SRTG, site.fPSA, site.CL, site.CG
        )
        CumulatedHeatGround += expCTTC * (radiationNext - radiationLast)
        CumulatedHeatBld += expCTTC * (solarRadiationBuilding(nextHour, climate) - solarRadiationBuilding(lastHour, climate))

    climate.QS['ground'][t] = CumulatedHeatGround * absorbGround * groundArea + CumulatedHeatBld * settings.AbsoRoof * site.AreaBuilding
    climate.QS['ground'][t] /= groundArea + site.AreaBuilding
    climate.QS['land'][t] = CumulatedHeatGround * settings.AbsoLandscape
    climate.QS['water'][t] = CumulatedHeatGround * settings.AbsoWater
    
    return {
        "ground": climate.QS['ground'][t],
        "land": climate.QS['land'][t],
        "water": climate.QS['water'][t]
    }

def solarRadiationBuilding(t: int, climate: ClimateInfo):
    """Building incident radiation per footprint area; zero is a valid RAD result."""
    if climate.solarBuilding is None:
        return climate.RadTotal[t]
    return climate.solarBuilding[t]

def solarRadiationTime(RadTotal, RadDiffuse, SVF, SDL, SDG=0.1, SRTL=0.4, SRTG=0.2, fPSA=0.2, CL=0.7, CG=0.8):
    """
    Calculates the adjusted solar radiation at time t (W/m2).

    :param RadTotal: Total radiation at time t
    :param RadDiffuse: Diffuse radiation at time t
    :param SVF: Average sky view factor in the area
    :param SDL: Shading percentage of landscape
    :param SDG: Shading percentage of urban construction (default=0.1)
    :param SRTL: Transparency of landscape (default=0.4)
    :param SRTG: Transparency of urban construction (default=0.2)
    :param fPSA: Shading percentage of buildings on the open space (default=0.2)
    :param CL: Heat transfer coefficient of landscape (default=0.7)
    :param CG: Heat transfer coefficient of urban construction (default=0.8)

    :return: Adjusted solar radiation at time t (W/m2)
    """
    return (((RadTotal - RadDiffuse) * (1 - fPSA) + RadDiffuse * SVF)
            * (1 - SDL * (1 - SRTL) * (1 - CL))
            * (1 - SDG * (1 - SRTG) * (1 - CG)))


def avgSolarAbsorpt(AreaRoad, AreaGround, AreaLandscape, AreaWater, AbsoRoad=0.74, AbsoGround=0.74,
                    AbsoLandscape=0.80, AbsoWater=0.96):
    """
    Calculates the average solar radiation absorption coefficient.

    :param AreaRoad: Area of road
    :param AreaGround: Area of ground
    :param AreaLandscape: Area of landscape
    :param AreaWater: Area of water
    :param AbsoRoad: Absorption coefficient of road (default=0.74, concrete)
    :param AbsoGround: Absorption coefficient of ground (default=0.74, concrete)
    :param AbsoLandscape: Absorption coefficient of landscape (default=0.80, grass)
    :param AbsoWater: Absorption coefficient of water (default=0.96)

    :return: Area-weighted average solar radiation absorption coefficient
    """
    totalArea = AreaRoad + AreaGround + AreaLandscape + AreaWater
    if totalArea <= 0:
        raise ValueError("The total surface area must be greater than zero.")

    return (AreaRoad * AbsoRoad + AreaGround * AbsoGround + AreaWater * AbsoWater
            + AreaLandscape * AbsoLandscape) / totalArea