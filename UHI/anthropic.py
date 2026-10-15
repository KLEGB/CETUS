from __future__ import annotations

from .modelInfo import SiteInfo, ClimateInfo, settingInfo
from .constant import *

def anthropicHeatFlowIntensity(t, climate: ClimateInfo, site: SiteInfo, settings: settingInfo):
    """
    Calculate the anthropogenic heat-flow intensity contributed by buildings and traffic, to the AIR (not the ground) in specific daytime t.
    :param t: current hour index.
    :param climate: climate info object.
    :param site: site info object.
    :param settings: settings object carrying load and traffic parameters.
    Prescribed coolingLoad is signed net building heat (load + electricity
    minus inward air/contact exchange), already normalized by site area.
    It is consumed once, without COP conversion or nonnegative clipping.
    :return: anthropogenic heat-flow intensity in W/m2.
    """
    car_heat = (
        settings.annualDistance
        * settings.FE
        * settings.fuelDensity
        * settings.NHC
        / 365
        / 24
        / 3600
    )
    car_heat = car_heat * site.AreaRoad * settings.intensity / site.TotalArea

    prescribed_load = climate.coolingLoad[t]
    if prescribed_load is None:
        cooling_load = _heatFlowIntensity(
            t,
            climate.Ta[t],
            site.plotRatio,
            settings.coolingSetPoint,
            settings.outSetPoint,
            settings.inSetPoint,
            # settings.qlx,
            settings.settingLoad,
            isWeekDay=settings.isWeekDay,
        )
        # cooling_load=0
        # climate.coolingLoad[t] = cooling_load
    else:
        cooling_load = float(prescribed_load)
    # print(site.plotRatio,t, climate.Ta[t], climate.Tb[t], cooling_load)
    totalLoadIntensity = (cooling_load + car_heat)
    climate.QA['ground'][t] = totalLoadIntensity / site.areaFractions['ground']
    climate.QA['land'][t] = 0
    climate.QA['water'][t] = 0

    return totalLoadIntensity


def _heatFlowIntensity(t, TaTMD, floorArea, coolingSetPoint=26, outSetPoint=35, inSetPoint=26,
                      settingLoad = 90,isWeekDay = True) -> float:
    """
    Calculate the building cooling anthropogenic heat intensity.
    :param t: current hour index.
    :param TaTMD: canopy air temperature in Celsius.
    :param floorArea: effective floor-area ratio contributing to load intensity.
    :param coolingLoad: optional prescribed cooling load intensity in W/m2.
    :param coolingSetPoint: cooling set point in Celsius.
    :param outSetPoint: outdoor reference temperature in Celsius.
    :param inSetPoint: indoor reference temperature in Celsius.
    :return: building anthropogenic heat intensity in W/m2.
    """
    coolingLoad = max(TaTMD - coolingSetPoint, 0) / (outSetPoint - inSetPoint) * settingLoad
    if isWeekDay:
        loadCoefficient = occDenseWeekDay['office'][t]/peakDense['office'] 
    else:
        loadCoefficient = occDenseWeekEnd['office'][t]/peakDense['office']

    # print(TaTMD, coolingSetPoint, outSetPoint, inSetPoint, loadCoefficient, settingLoad)
    # print(coolingLoad* floorArea * loadCoefficient)
    return coolingLoad * floorArea * loadCoefficient
