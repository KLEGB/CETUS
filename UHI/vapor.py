from __future__ import annotations
import numpy as np
from .modelInfo import SiteInfo, ClimateInfo, settingInfo
from .aT import combinedConvectionCoefficient,airDensity,referenceVelocity,saturationVaporPressure,cH
from .constant import heatBalanceConstants,landMaximumVaporAvailability,vaporSpecificGasConstant,groundMaximumVaporAvailability

def vaporHeatFlowIntensity(t, climate: ClimateInfo, site: SiteInfo,setting:settingInfo):
    """
    The vapor heat flow intensity (W/m2) to the AIR (not the ground) in specific daytime t, 
    which is the sum of the heat flow intensity from the landscape, water, ground and roof.
    :param climate: climate info object
    :param site: site info object
    :param setting: setting info object
    :param t: current time
    :return: The vapor heat flow intensity (W/m2) to the AIR in specific daytime t
    """
    vaporDiffLand = vaporPressureDeficit(climate.Ta[t-1], climate.Rh[t], climate.Tg_split['land'][t-1])
    vaporLandscape = evaporationRate(vaporDiffLand,climate.QS['land'][t-1],climate.Ta[t-1],climate.Pressure[t],
                                     referenceVelocity(climate.Velocity[t], site.kS, setting.a, setting.k), 
                                     setting.rsveg,setting.zRef, setting.z0)
    vaporDiffWater = vaporPressureDeficit(climate.Ta[t-1], climate.Rh[t], climate.Tg_split['water'][t-1])
    vaporWater = evaporationRate(vaporDiffWater,climate.QS['water'][t-1],climate.Ta[t-1],climate.Pressure[t],
                                     referenceVelocity(climate.Velocity[t], site.kS, setting.a, setting.k), 
                                     0.0,setting.zRef, setting.z0)
    vaporDiffGround = vaporPressureDeficit(climate.Ta[t-1], climate.Rh[t], climate.Tg_split['ground'][t-1])
    vaporGround = evaporationRate(vaporDiffGround,climate.QS['ground'][t-1],climate.Ta[t-1],climate.Pressure[t],
                                     referenceVelocity(climate.Velocity[t], site.kS, setting.a, setting.k), 
                                     setting.rsground,setting.zRef, setting.z0)
    
    vaporRoof = vaporLandscape * setting.gRVEff

    # rat = latenEvaporationFixRatio(climate.Ta[t-1], climate.Rh[t-1], climate.Velocity[t], site.kS, setting.a, setting.k,setting.aw,setting.bw,setting.baseLETa,setting.baseLERH,setting.baseLEVelocity)
    # vaporLandscape = rat * site.vaporLandscape[t]
    # vaporWater = rat * site.vaporWater[t]
    # vaporGround = rat * site.vaporGround[t]
    # vaporRoof = rat * site.vaporRoof[t]

    # heatFlowIntensity= (climate.Ta[t-1],
    #                                site.AreaWater, site.AreaBuilding, site.AreaLandscape, site.AreaGround+site.AreaRoad,
    #                                site.vaporWater[t],site.vaporLandscape[t],site.vaporGround[t],site.vaporRoof[t],
    #                                site.greenRoofPercentage,site.groundInfiltratePercentage
    #                                )
    # print(t,vaporLandscape, vaporWater, vaporGround)
    IlatT = (site.AreaLandscape * vaporLandscape 
             + site.AreaWater * vaporWater
             + setting.gInff * site.AreaGround * vaporGround
            #  + setting.gInff * site.AreaBuilding * vaporGround
             + site.greenRoofPercentage * site.AreaBuilding * vaporRoof)/ (site.AreaWater + site.AreaBuilding + site.AreaLandscape + site.AreaGround)
    # IlatT *= latentHeat(climate.Ta[t-1]) / 3.6
    # print(IlatT)
    climate.QV['ground'][t] = -vaporGround
    climate.QV['land'][t] = -vaporLandscape
    climate.QV['water'][t] = -vaporWater
    return - IlatT

def evaporationRate(VPD, Qs, TaTMD, PaTMD, VaReference, rs,zRef = 10, z0 = 0.2):
    """
    Evaporation potential (W/m2) in specific daytime t, calculated by the Penman-Monteith equation (adjusted by FAO-56).
    :param Qs: validated shortwave radiation heat intensity (W/m2) on the surface in specific daytime t, given by the shortWaveRadHeatIntensity function.
    :param TaTMD: dry bulb temperature in specific daytime t
    :param VPD: vapor density deficit in specific daytime t
    :param PaTMD: air pressure in specific daytime t
    :param VaReference: reference wind speed in specific daytime t
    :param rs: surface resistance (s/m) for different surfaces (e.g., vegetation, water, ground)
    :param zRef: reference wind height in m.
    :param z0: aerodynamic roughness length in m.
    """

    energyStimulation = vapoorResponse(TaTMD) * Qs
    ra = 1 / (VaReference * cH(zRef, z0))
    vaporStimulation = airDensity(PaTMD, TaTMD) * heatBalanceConstants['c_p'] * VPD / ra
    roughnessFactor =  1 + rs / ra
    # print((energyStimulation , vaporStimulation))
    # print(vapoorResponse(TaTMD), psychrometricConstant(PaTMD,TaTMD) , roughnessFactor)
    # print(latentHeat(TaTMD))
    return (energyStimulation + vaporStimulation) / (vapoorResponse(TaTMD) + psychrometricConstant(PaTMD,TaTMD) * roughnessFactor)

def vaporPressureDeficit(targetTa, RHaTMD, sourcesTa):
    """
    Vapor pressure deficit (kPa)
    :param targetTa: target dry bulb temperature in specific daytime t
    :param RHaTMD: relative humidity in specific daytime t
    :param sourcesTa: source dry bulb temperature in specific daytime t
    """
    # Tw = wetbulbTemperature(TaTMD, RHaTMD, PaTMD)
    # vpd = saturationVaporPressure(TaTMD) - saturationVaporPressure(Tw)
    vpd = saturationVaporPressure(sourcesTa) - saturationVaporPressure(targetTa) * RHaTMD / 100 
    return max(vpd, 0)

def latentHeat(TaTMD):
    # return 2491.146 - 2.302 * TaTMD
    """
    latent heat of vaporization (kJ/kg) as a function of temperature (°C)
    :param TaTMD: dry bulb temperature in specific daytime t
    """
    return 2500.8 - 2.36 * TaTMD + 0.0016 * TaTMD**2 - 0.00006 * TaTMD**3

def vapoorResponse(TaTMD):
    """
    slope of saturation vapor pressure curve, by FAO-56, kPa/°C
    :param TaTMD: dry bulb temperature in specific daytime t (°C)
    """
    vpR = saturationVaporPressure(TaTMD) * 4098
    vpR /= (TaTMD + 237.3) ** 2
    return vpR

def psychrometricConstant(PaTMD,TaTMD):
    """
    Psychrometric constant (kPa/°C)
    :param PaTMD: air pressure in specific daytime t (Pa)
    :param TaTMD: dry bulb temperature in specific daytime t (°C)
    """
    # return 0.000665 * PaTMD / 1000
    return (heatBalanceConstants['c_p'] * PaTMD/1000) / (0.622 * latentHeat(TaTMD)* 1000)