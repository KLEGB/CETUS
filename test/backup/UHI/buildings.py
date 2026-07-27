from __future__ import annotations

import numpy as np
from .site import SiteInfo, ClimateInfo
from .aT import airHeatTransferCoefficient
from .constant import carHeatParameters,qlx

def activityDifference(t, climate: ClimateInfo, site: SiteInfo, ReferenceHeight=100, a=0.22, k=0.1,
                    _carParams=None, coolingSetPoint=26, outSetPoint=34.5, inSetPoint=29.9,
                    loadCoefficient=qlx):
    if _carParams is None:
        _carParams = carHeatParameters
    carHeat = _carParams['annualDistance'] * _carParams['FE'] * _carParams['fuelDensity'] * \
              _carParams['NHC'] / 365 / 24 / 3600
    carHeat = carHeat * site.AreaRoad * _carParams['intensity']
    HFI = HeatFlowIntensity(t, climate.Ta[t],site.TotalArea * site.plotRatio,site.coolingLoad,coolingSetPoint, outSetPoint, inSetPoint,
                    loadCoefficient) + carHeat / site.TotalArea
    aHC = airHeatTransferCoefficient(climate.Ta[t], climate.Velocity[t], site, ReferenceHeight, a, k)

    return HFI / aHC


def HeatFlowIntensity(t, TaTMD, floorArea, coolingLoad=None, coolingSetPoint=26, outSetPoint=34.5, inSetPoint=29.9,
                      loadCoefficient=qlx) -> float:
    if coolingLoad is None:
        coolingLoad = max(TaTMD - coolingSetPoint, 0) / (outSetPoint - inSetPoint) * loadCoefficient[t]
    # print('coolingLoad', coolingLoad)
    # print('floorArea', floorArea)
    return coolingLoad * floorArea
