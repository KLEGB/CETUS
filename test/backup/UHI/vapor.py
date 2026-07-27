from __future__ import annotations

import numpy as np
from .site import SiteInfo, ClimateInfo
from .aT import airHeatTransferCoefficient

def vaporDifference(t, climate: ClimateInfo, site: SiteInfo, ReferenceHeight=100, a=0.22, k=0.1):
    return -HeatFlowIntensity(climate.Ta[t],
                                   site.AreaWater, site.AreaBuilding, site.AreaLandscape, site.AreaGround+site.AreaRoad,
                                   site.vaporWater[t],site.vaporLandscape[t],site.vaporGround[t],site.vaporRoof[t],
                                   site.greenRoofPercentage,site.groundInfiltratePercentage
                                   )/airHeatTransferCoefficient(climate.Ta[t], climate.Velocity[t], site, ReferenceHeight, a, k)

def HeatFlowIntensity(TaTMD,
                            AreaWater, AreaBuilding, AreaLandscape, AreaGround,
                            vaporWater, vaporLandscape, vaporGround, vaporRoof,
                            greenRoofPercentage, groundInfiltratePercentage):
    IlatT = (AreaLandscape * vaporLandscape + AreaWater * vaporWater
             + groundInfiltratePercentage * AreaGround * vaporGround
             + greenRoofPercentage * AreaBuilding * vaporRoof)
    IlatT *= LatentHeat(TaTMD) / (3.6 * (AreaWater + AreaBuilding + AreaLandscape + AreaGround))
    return IlatT


def LatentHeat(TaTMD):
    return 2491.146 - 2.302 * TaTMD
