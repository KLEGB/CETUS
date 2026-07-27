from __future__ import annotations

from collections.abc import Iterable
import numpy as np
from .constant import *


class settingsInfo(object):
    __slots__ = ['qlx',
                 'intensity', 'annualDistance', 'FE', 'fuelDensity', 'NHC',
                 'a', 'k', 'ReferenceHeight', 'coolingSetPoint', 'outSetPoint', 'inSetPoint']

    def __init__(self, qlxSeries=None,
                 intensity=None, annualDistance=None, FE=None, fuelDensity=None, NHC=None,
                 a=None, k=None, ReferenceHeight=None, coolingSetPoint=None, outSetPoint=None, inSetPoint=None):
        self.qlx = listObj2List(qlx if qlxSeries is None else qlxSeries)
        self.intensity = carHeatParameters['intensity'] if intensity is None else intensity
        self.annualDistance = carHeatParameters['annualDistance'] if annualDistance is None else annualDistance
        self.FE = carHeatParameters['FE'] if FE is None else FE
        self.fuelDensity = carHeatParameters['fuelDensity'] if fuelDensity is None else fuelDensity
        self.NHC = carHeatParameters['NHC'] if NHC is None else NHC
        self.a = defaultValues['a'] if a is None else a
        self.k = 0.1 if k is None else k
        self.ReferenceHeight = defaultValues['ReferenceHeight'] if ReferenceHeight is None else ReferenceHeight
        self.coolingSetPoint = defaultValues['coolingSetPoint'] if coolingSetPoint is None else coolingSetPoint
        self.outSetPoint = defaultValues['outSetPoint'] if outSetPoint is None else outSetPoint
        self.inSetPoint = defaultValues['inSetPoint'] if inSetPoint is None else inSetPoint

    def car_params_dict(self):
        return {
            'intensity': self.intensity,
            'annualDistance': self.annualDistance,
            'FE': self.FE,
            'fuelDensity': self.fuelDensity,
            'NHC': self.NHC,
        }

    def default_values_dict(self):
        return {
            'a': self.a,
            'k': self.k,
            'ReferenceHeight': self.ReferenceHeight,
            'coolingSetPoint': self.coolingSetPoint,
            'outSetPoint': self.outSetPoint,
            'inSetPoint': self.inSetPoint,
        }

    def to_calculate_kwargs(self):
        values = self.default_values_dict()
        values['loadCoefficient'] = np.array(self.qlx, copy=True)
        values['_carParams'] = self.car_params_dict()
        return values


class SiteInfo(object):
    __slots__ = ['TotalArea',
                 'AreaBuilding','AreaFacade','AreaRoad','AreaGround','AreaLandscape','AreaWater','AreaTreeShading',
                 'kS',
                 'greenRoofPercentage', 'groundInfiltratePercentage',
                 'AbsoRoad','AbsoGround','AbsoLandscape','AbsoWater',
                 'vaporWater', 'vaporLandscape', 'vaporGround', 'vaporRoof','coolingLoad',
                 'SVF','plotRatio','SDL','SDG','SRTL','SRTG','fPSA','CL','CG']

    """
    :param AreaBuilding: absolute building occupied area. Under default initialization it is derived from 40% of total area.
    :param AreaRoad: absolute road area. Under default initialization it is derived from the default site composition.
    :param AreaGround: absolute open ground area. Under default initialization it is derived from the default site composition.
    :param AreaLandscape: absolute landscape area. Under default initialization it is derived from the default site composition.
    :param AreaWater: absolute water area. Under default initialization it is derived from the default site composition.
    :param kS: ventilation blockage ratio of the site, used with settingsInfo.k to derive the effective wind reduction factor.
        The default is `0.3 * SiteInfo.AreaBuilding / SiteInfo.TotalArea`, i.e. 0.3 times building density.
    :param greenRoofPercentage: area percentage of greening on the roof (default: 10%)
    :param groundInfiltratePercentage: area percentage of ground which allow infiltration (default: 50%)
    :param SVF: Average Sky View Factor in the area (default: 0.7)
    :param plotRatio: ratio of building gross floor area to total area (default: 1.2)
    :param coolingLoad: average cooling load of the site, in W/m2 (default: None)
    
    :param AbsoRoad: absorption coefficient of road (default=0.74, concrete)
    :param AbsoGround: absorption coefficient of ground (default=0.74, concrete)
    :param AbsoLandscape: absorption coefficient of landscape (default=0.80, grass)
    :param AbsoWater: absorption coefficient of water (default=0.96)
    
    :param vaporWater: evaporation of water (kg/m2-h) (default set as series)
    :param vaporGround: evaporation of ground (kg/m2-h) (default set as series)
    :param vaporLandscape: evaporation of landscape (kg/m2-h) (default set as series)
    :param vaporRoof: evaporation of roof (kg/m2-h) (default set as series)
    
    :param SDG: Shading percentage of urban construction (default=0.1)
    :param SRTL: Transparency of lanscape (default: 0.4)
    :param SRTG: Transparency of urban construction (default: 0.2)
    :param fPSA: Shading percentage of building on the open space (default: 0.2)
    :param CL: Heat transfer coefficient of lanscape (default: 0.7)
    :param CG: Heat transfer coefficient of urban construction (default: 0.8)
    """
    def __init__(self,totalArea=1000,bldPercentage=None,roadPercentage=None,groundPercentage=None,landscapePercentage=None,waterPercentage=None,coolingLoad=None):
        if bldPercentage is None:
            bldPercentage=0.4
        if landscapePercentage is None:
            landscapePercentage=0.3
        if waterPercentage is None:
            waterPercentage=0.05
        scalePercentage = bldPercentage + landscapePercentage + waterPercentage
        if bldPercentage + landscapePercentage + waterPercentage > 0.8:
            bldPercentage *= 0.8 / scalePercentage
            landscapePercentage *= 0.8 / scalePercentage
            waterPercentage *= 0.8 / scalePercentage
            roadPercentage_default = 0.05
            groundPercentage_default = 0.15
        else:
            roadPercentage_default=0.25*(1-scalePercentage)
            groundPercentage_default=0.75*(1-scalePercentage)
        if groundPercentage is None:
            groundPercentage=groundPercentage_default
        if roadPercentage is None:
            roadPercentage=roadPercentage_default
        sumPercentage = bldPercentage + landscapePercentage + waterPercentage + roadPercentage + groundPercentage

        self.TotalArea=totalArea
        self.AreaBuilding = self.TotalArea*bldPercentage/sumPercentage
        self.AreaRoad = self.TotalArea*roadPercentage/sumPercentage
        self.AreaGround = self.TotalArea*groundPercentage/sumPercentage
        self.AreaLandscape = self.TotalArea*landscapePercentage/sumPercentage
        self.AreaWater = self.TotalArea*waterPercentage/sumPercentage

        self.coolingLoad=coolingLoad

        self.AreaTreeShading = self.AreaLandscape
        self.kS = 0.3 * self.AreaBuilding / self.TotalArea
        self.SVF = 0.7
        self.plotRatio=bldPercentage*3
        self.AreaFacade=0.5*self.TotalArea

        self.AbsoRoad = 0.74
        self.AbsoGround = 0.74
        self.AbsoLandscape = 0.80
        self.AbsoWater = 0.96

        # total:12.52
        self.vaporWater = listObj2List(defVaporWater)
        # total:7.69
        self.vaporLandscape = listObj2List(defVaporLandscape)
        # total:1.69
        self.vaporGround = listObj2List(defVaporGround)
        # total:6.15
        self.vaporRoof = listObj2List(defVaporRoof)

        self.greenRoofPercentage = 0.10
        self.groundInfiltratePercentage = 0.50

        self.SDL = self.AreaLandscape/self.TotalArea
        self.SDG = 0.1
        self.SRTL = 0.4
        self.SRTG = 0.2
        self.fPSA = 0.2
        self.CL = 0.7
        self.CG = 0.8

class ClimateInfo(object):
    __slots__ =['RadTotal','RadDiffuse','Velocity','Ta','Rh']
    """
    All climate information should be a 24 length series of floats for each params.
    """

    def __init__(self,Ta=None,Velocity=None,RadTotal=None,RadDiffuse=None,Rh=None):
        self.RadTotal:np.ndarray[np.float32]=listObj2List(RadTotal) if RadTotal is not None else listObj2List(defRadTotal)
        self.RadDiffuse:np.ndarray[np.float32]=listObj2List(RadDiffuse) if RadDiffuse is not None else listObj2List(defRadDiffuse)
        self.Velocity:np.ndarray[np.float32]=listObj2List(Velocity) if Velocity is not None else listObj2List(defVelocity)
        self.Ta:np.ndarray[np.float32]=listObj2List(Ta) if Ta is not None else listObj2List(defTa)
        self.Rh:np.ndarray[np.float32]=listObj2List(Rh) if Rh is not None else listObj2List(defRh)



def listObj2List(listObj):
    if isinstance(listObj, np.ndarray):
        values = listObj.tolist()
    elif isinstance(listObj, Iterable) and not isinstance(listObj, (str, bytes)):
        values = list(listObj)
    else:
        values = [listObj]

    if len(values) == 0:
        values = [0.0]

    while len(values) < 24:
        values.append(values[-1])

    return np.array(values[:24], dtype=np.float32)

