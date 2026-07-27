def referenceVentilationBlockage(kS, k=0.1):
    """
    Calculate the effective wind reduction factor used by the reference wind velocity.
    :param kS: ventilation blockage ratio of the site.
    :param k: building ventilation overhead ratio in the design scope (default: 0.1)
    :return: effective wind reduction factor derived from ventilation blockage.
    """
    return -0.507 * kS + 0.244 * k + 0.697


def AvgHeatTransfer(VaTMD, site, a=0.22, k=0.1):
    """
    Average surface heat transfer coefficient (W/m2-K)
    Following equation B.0.2-3 in JGJ 286-2013
    :param VaTMD: wind speed in specific daytime t
    :param site: site info object carrying `kS`, the site ventilation blockage ratio
    :param a: roughness of the ground, 0.12 for sea, 0.16 for subUrban, 0.22 for Urban,0.30 for Central City (default: 0.22)
    :param k: building ventilation overhead ratio in the design scope (default: 0.1)
    :return: Aheat transfer coefficient in specific daytime t
    """
    return 10.9 + 4.1 * referenceVelocity(VaTMD, site, a, k)


def referenceVelocity(VaTMD, site, a=0.22, k=0.1):
    """
    reference wind velocity on 1.5m height in specific daytime t
    :param VaTMD: wind speed in specific daytime t
    :param site: site info object carrying `kS`, the site ventilation blockage ratio
    :param a: roughness of the ground, 0.12 for sea, 0.16 for subUrban, 0.22 for Urban,0.30 for Central City (default: 0.22)
    :param k: building ventilation overhead ratio in the design scope (default: 0.1)
    :return: reference wind speed in specific daytime t
    """
    return (0.15 ** a) * VaTMD * referenceVentilationBlockage(site.kS, k)


def airHeatTransferCoefficient(TaTMD, VaTMD, site, ReferenceHeight=100, a=0.22, k=0.1) -> float:
    """
    Average air heat transfer coefficient (W/m2-K)
    Following equation B.0.2-3 in JGJ 286-2013
    :param TaTMD: dry bulb temperature in specific daytime t
    :param VaTMD: wind speed in specific daytime t
    :param site: site info object carrying `kS`, the site ventilation blockage ratio
    :param ReferenceHeight: reference height that where the maximum air can the heat be transferred (default: 100)
    :param a: roughness of the ground, 0.12 for sea, 0.16 for subUrban, 0.22 for Urban,0.30 for Central City (default: 0.22)
    :param k: building ventilation overhead ratio in the design scope (default: 0.1)
    :return: Air heat transfer coefficient in specific daytime t
    """
    return 1.005 * ReferenceHeight * (-0.0039 * TaTMD + 1.2822) * AvgHeatTransfer(VaTMD, site, a, k)
