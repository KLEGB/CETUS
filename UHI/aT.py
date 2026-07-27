from .constant import heatBalanceConstants
import numpy as np

def referenceVentilationBlockage(kS, k=0.1):
    """
    Calculate the effective wind reduction factor used by the reference wind velocity.
    :param kS: ventilation blockage ratio of the site.
    :param k: building ventilation overhead ratio in the design scope (default: 0.1)
    :return: effective wind reduction factor derived from ventilation blockage.
    """
    return -0.507 * kS + 0.244 * k + 0.697


def avgHeatTransfer(VaTMD, kS, a=0.22, k=0.1):
    """
    Average surface heat transfer coefficient (W/m2-K)
    Following equation B.0.2-3 in JGJ 286-2013
    :param VaTMD: wind speed in specific daytime t
    :param kS: site ventilation blockage ratio
    :param a: roughness of the ground, 0.12 for sea, 0.16 for subUrban, 0.22 for Urban,0.30 for Central City (default: 0.22)
    :param k: building ventilation overhead ratio in the design scope (default: 0.1)
    :return: Aheat transfer coefficient in specific daytime t
    """
    return 10.9 + 4.1 * referenceVelocity(VaTMD, kS, a, k)


def referenceVelocity(VaTMD, kS, a=0.22, k=0.1):
    """
    reference wind velocity on 1.5m height in specific daytime t
    :param VaTMD: wind speed in specific daytime t
    :param kS: site ventilation blockage ratio
    :param a: roughness of the ground, 0.12 for sea, 0.16 for subUrban, 0.22 for Urban,0.30 for Central City (default: 0.22)
    :param k: building ventilation overhead ratio in the design scope (default: 0.1)
    :return: reference wind speed in specific daytime t
    """
    return (0.15 ** a) * VaTMD * referenceVentilationBlockage(kS, k)


def airHeatTransferCoefficient(TaTMD, VaTMD, site, Hair=100, a=0.22, k=0.1) -> float:
    """
    Average air heat transfer coefficient (W/m2-K)
    Following equation B.0.2-3 in JGJ 286-2013
    :param TaTMD: dry bulb temperature in specific daytime t
    :param VaTMD: wind speed in specific daytime t
    :param kS: site ventilation blockage ratio
    :param Hair: reference height that where the maximum air can the heat be transferred (default: 100)
    :param a: roughness of the ground, 0.12 for sea, 0.16 for subUrban, 0.22 for Urban,0.30 for Central City (default: 0.22)
    :param k: building ventilation overhead ratio in the design scope (default: 0.1)
    :return: Air heat transfer coefficient in specific daytime t
    """
    return 1.005 * Hair * (-0.0039 * TaTMD + 1.2822) * avgHeatTransfer(VaTMD, site.kS, a, k)


def airDensity(pressure, airTemperature):
    """
    Dry-air density from the ideal-gas law.
    :param pressure: air pressure in Pa.
    :param airTemperature: canopy air temperature in Celsius.
    :return: air density in kg/m3.
    """
    density = float(pressure) / (heatBalanceConstants['R_d'] * float(airTemperature+273.15))
    return density

def combinedConvectionCoefficient(TgTMD, TaTMD, PaTMD, VaReference, c1,zRef = 10, z0 = 0.2):
    """
    Combined convection coefficient (W/m2-K)
    :param TgTMD: surface temperature in specific daytime t
    :param TaTMD: dry bulb temperature in specific daytime t
    :param PaTMD: air pressure in specific daytime t
    :param VaReference: reference wind speed in specific daytime t
    :param transferCoefficient: bulk transfer coefficient under a neutral atmospheric stability assumption.
    :param c1: constant used to calculate the combined convection coefficient (default: 0.001)
    :param zRef: reference wind height in m.
    :param z0: aerodynamic roughness length in m.
    :return: combined convection coefficient in specific daytime t
    """
    alpha_natural = float(c1) * abs(float(TgTMD) - float(TaTMD)) ** (1.0 / 3.0)
    alpha_forced = airDensity(PaTMD, TaTMD) * heatBalanceConstants['c_p'] * cH(zRef, z0) * VaReference
    alpha_contact = float(heatBalanceConstants['alphaContact'])
    return np.sqrt(alpha_natural **2 + alpha_forced **2) + alpha_contact

# def canopyVentilationCoefficient(PaTMD, TaTMD_bg, VaTMD):
#     """
#     Canopy ventilation coefficient (W/m2-K)
#     :param PaTMD: air pressure in specific daytime t
#     :param TaTMD_bg: background dry bulb temperature in specific daytime t
#     :param VaTMD: wind speed in specific daytime t
#     :return: canopy ventilation coefficient in specific daytime t
#     """
#     return airDensity(PaTMD, TaTMD_bg) * heatBalanceConstants['c_p'] * VaTMD


def cH(zRef = 10, z0 = 0.2):
    """
    Bulk transfer coefficient under a neutral atmospheric stability assumption.
    :param zRef: reference wind height in m.
    :param z0: aerodynamic roughness length in m.
    :return: dimensionless bulk transfer coefficient.
    """
    if zRef <= z0:
        raise ValueError("zRef must be greater than z0")
    return heatBalanceConstants['kappa'] ** 2 / (np.log(float(zRef) / float(z0)) ** 2)

def saturationVaporPressure(tempCelsius):
    """
    Tetens equation: convert temperature (degC) to saturation vapor pressure (kPa).

    This function is the single entry point for the formula to avoid duplicated constants.
    :param tempCelsius: Temperature in degrees Celsius
    :return: Saturation vapor pressure in kPa
    """
    return 0.6108 * np.exp(17.27 * tempCelsius / (tempCelsius + 237.3))