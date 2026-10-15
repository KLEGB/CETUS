from __future__ import annotations

from .aT import referenceVelocity, airDensity, combinedConvectionCoefficient
from .constant import heatBalanceConstants
from .modelInfo import ClimateInfo, SiteInfo, settingInfo


def _safeDenominator(value, minimum=1e-6):
    """
    Prevent divide-by-zero in the canopy-air update when the aerodynamic exchange collapses.
    :param value: raw denominator value.
    :param minimum: minimum positive magnitude allowed in the denominator.
    :return: protected positive denominator.
    """
    return max(float(value), float(minimum))


def _clampTemperature(temperature, minimum=-73.15, maximum=100.0):
    """
    Keep iterative temperatures inside a broad physical range to avoid numerical blow-up.
    :param temperature: trial temperature in Celsius.
    :param minimum: lower temperature bound in Celsius.
    :param maximum: upper temperature bound in Celsius.
    :return: clamped temperature in Celsius.
    """
    return min(max(float(temperature), float(minimum)), float(maximum))


def weighted(valuesDict, areaFractions):
    """
    Area-weight one state variable across the three isolated surface processes.
    :param surfaceStates: dictionary keyed by surface type.
    :param areaFractions: dictionary keyed by surface type.
    :param stateKey: field to read from each surface state.
    :return: area-weighted state value.
    """
    return sum(float(areaFractions[name]) * float(valuesDict[name]) for name in areaFractions.keys())


def approximateTsrf(t,Ta_setting,climate:ClimateInfo,site:SiteInfo,settings:settingInfo,surfaceName, srfHeatFlux):
    """
    approximiate the surface temperature for a fixed canopy air temperature by bisection.
    :param t: current hour index.
    :param Ta_setting: setting air temperature to solve, Celsius.
    :param climate: climate info object.
    :param site: site info object.
    :param settings: validated heat-balance settings.
    :param surfaceName: one of `ground`, `water`, `land`.
    :param srfHeatFlux: net surface heat gain in W/m2.
    :return: converged surface temperature in Celsius.
    """
    if surfaceName == 'ground':
        conductiveCoefficient = float(settings.UDeepGround)
        storageCapacity = float(settings.CGround)
        Tg_previous = climate.Tg_split['ground'][t-1]
    elif surfaceName == 'water':
        conductiveCoefficient = float(settings.UDeepWater)
        storageCapacity = float(settings.CWater)
        Tg_previous = climate.Tg_split['water'][t-1]
    elif surfaceName == 'land':
        conductiveCoefficient = float(settings.UDeepLand)
        storageCapacity = float(settings.CLand)
        Tg_previous = climate.Tg_split['land'][t-1]
    else:
        raise ValueError(f"unsupported surfaceName: {surfaceName}")
    refVelocity = referenceVelocity(climate.Velocity[t], site.kS, settings.a, settings.k)
    def srfBalance(srfTemp):
        convectionCoefficient = combinedConvectionCoefficient(
        srfTemp,
        Ta_setting,
        climate.Pressure[t],
        refVelocity,
        settings.c1,
        settings.zRef,
        settings.z0,
        )
        temperatureGap = float(srfTemp) - Ta_setting
        delta = convectionCoefficient * temperatureGap 
        delta += float(conductiveCoefficient) * (float(srfTemp) - float(settings.GroundTemperature))
        delta -= float(srfHeatFlux)
        if settings.dt is not None:
            delta += storageCapacity * (srfTemp - Tg_previous) / settings.dt
        return delta
    

    # define innitial lower and upper bounds for the bisection method
    lowerTemperature = float(settings.GroundTemperature) - 50.0
    upperTemperature = float(settings.GroundTemperature) + 200.0
    lowerResidual = srfBalance(lowerTemperature)
    upperResidual = srfBalance(upperTemperature)
    expansionCount = 0

    while lowerResidual > 0 or upperResidual < 0:
        lowerTemperature -= 50.0
        upperTemperature += 50.0
        lowerResidual = srfBalance(lowerTemperature)
        upperResidual = srfBalance(upperTemperature)
        expansionCount += 1
        if expansionCount > 20:
            raise RuntimeError("failed to bracket the surface temperature solution")
    
    # iterate using bisection method until the solution converges within the specified tolerance
    while (upperTemperature - lowerTemperature) > float(settings.innerTolerance):
        middleTemperature = 0.5 * (lowerTemperature + upperTemperature)
        middleResidual = srfBalance(middleTemperature)
        if middleResidual < 0:
            lowerTemperature = middleTemperature
        else:
            upperTemperature = middleTemperature


    # record
    climate.QGFlux[surfaceName][t] = float(conductiveCoefficient) * (float(middleTemperature) - float(settings.GroundTemperature))
    climate.Qsg[surfaceName][t] = storageCapacity * (middleTemperature - Tg_previous) / settings.dt if settings.dt is not None else 0.0
    
    return 0.5 * (lowerTemperature + upperTemperature)

def approximateTa(t, srfHeatFlux, airHeatFlux, climate: ClimateInfo, site: SiteInfo, settings: settingInfo):
    """
    Solve the coupled surface and shared canopy-air heat balance.
    :param t: current time-step index.
    :param srfHeatFlux: surface heat flux dictionary in W/m2.
    :param airHeatFlux: canopy-air heat source or sink in W/m2.
    :param climate: climate information object.
    :param site: site information object.
    :param settings: validated heat-balance settings.
    :return: solved surface and shared canopy-air temperatures.
    """
    surfaceNames = ("ground", "water", "land")
    pressure = float(climate.Pressure[t])
    backgroundTemperature = float(climate.Tb[t])
    previousAirTemperature = float(climate.Ta[t - 1])
    maxIterations = max(1, int(settings.outerMaxIter))
    temperatureTolerance = max(1.0e-6, float(settings.outerTolerance))
    balanceTolerance = max(1.0e-6, float(getattr(settings, "balanceTolerance", 0.1)))

    # calculate the near-surface reference velocity
    refVelocity = referenceVelocity(climate.Velocity[t], site.kS, settings.a, settings.k)

    # airHeatFlux may be either a grid-average value or a surface dictionary
    qAir = float(weighted(airHeatFlux, site.areaFractions)) if isinstance(airHeatFlux, dict) else float(airHeatFlux)

    if settings.dt is None:
        dt = None
    else:
        dt = float(settings.dt)
        if dt <= 0.0:
            raise ValueError("settings.dt must be positive and expressed in seconds.")

    def evaluateBalance(Ta):
        """Evaluate the coupled canopy-air heat-balance residual for a trial Ta."""
        Ta = float(_clampTemperature(Ta))
        Tg, alphaGroundAir, sensibleHeatFlux = {}, {}, {}

        for surfaceName in surfaceNames:
            # solve each surface temperature using the same canopy-air temperature
            Tg[surfaceName] = float(_clampTemperature(
                approximateTsrf(t, Ta, climate, site, settings, surfaceName, srfHeatFlux[surfaceName])
            ))

            alphaGroundAir[surfaceName] = float(_safeDenominator(
                combinedConvectionCoefficient(
                    Tg[surfaceName], Ta, pressure, refVelocity,
                    settings.c1, settings.zRef, settings.z0
                )
            ))

            sensibleHeatFlux[surfaceName] = alphaGroundAir[surfaceName] * (Tg[surfaceName] - Ta)

        # calculate the area-weighted sensible heat transferred to canopy air
        weightedSensibleHeatFlux = float(weighted(sensibleHeatFlux, site.areaFractions))

        # calculate canopy-air ventilation exchange with the background atmosphere
        alphaCanopy = float(_safeDenominator(
            combinedConvectionCoefficient(
                backgroundTemperature, Ta, pressure, climate.Velocity[t],
                settings.c1, settings.zRefd, settings.z0d
            )
        ))
        ventilationHeatFlux = alphaCanopy * (Ta - backgroundTemperature)

 
        # calculate canopy-air storage
        if dt is None:
            storageCoefficient, storageHeatFlux = 0.0, 0.0
        else:
            storageCoefficient = (
                airDensity(pressure, Ta) * heatBalanceConstants["c_p"]
                * float(settings.HAir) / dt
            )
            storageHeatFlux = storageCoefficient * (Ta - previousAirTemperature)

        # canopy-air balance: surface sensible heat + air source - ventilation - storage
        residual = weightedSensibleHeatFlux + qAir - ventilationHeatFlux - storageHeatFlux
        return {
            "Ta": Ta,
            "Tg": Tg,
            "alphaGroundAir": alphaGroundAir,
            "QH": sensibleHeatFlux,
            "QHWeighted": weightedSensibleHeatFlux,
            "alphaCanopy": alphaCanopy,
            "QV": ventilationHeatFlux,
            "storageCoefficient": storageCoefficient,
            "Qsa": storageHeatFlux,
            "AirHeatFlux": qAir,
            "residual": residual,
        }

    # construct an initial temperature bracket
    initialLower = min(previousAirTemperature, backgroundTemperature)
    initialUpper = max(previousAirTemperature, backgroundTemperature)
    bracketSpan = 5.0
    lowerTemperature = float(_clampTemperature(initialLower - bracketSpan))
    upperTemperature = float(_clampTemperature(initialUpper + bracketSpan))
    lowerState = evaluateBalance(lowerTemperature)
    upperState = evaluateBalance(upperTemperature)

    # expand the bracket until the residual changes sign
    for _ in range(8):
        lowerResidual, upperResidual = lowerState["residual"], upperState["residual"]
        if abs(lowerResidual) <= balanceTolerance or abs(upperResidual) <= balanceTolerance or lowerResidual * upperResidual < 0.0:
            break

        previousLower, previousUpper = lowerTemperature, upperTemperature
        bracketSpan *= 2.0
        lowerTemperature = float(_clampTemperature(initialLower - bracketSpan))
        upperTemperature = float(_clampTemperature(initialUpper + bracketSpan))

        if lowerTemperature == previousLower and upperTemperature == previousUpper:
            break

        lowerState = evaluateBalance(lowerTemperature)
        upperState = evaluateBalance(upperTemperature)

    lowerResidual, upperResidual = lowerState["residual"], upperState["residual"]
    finalState = None

    if abs(lowerResidual) <= balanceTolerance:
        finalState = lowerState
    elif abs(upperResidual) <= balanceTolerance:
        finalState = upperState
    elif lowerResidual * upperResidual < 0.0:
        # solve the nonlinear balance equation using bisection
        for iteration in range(maxIterations):
            middleTemperature = 0.5 * (lowerTemperature + upperTemperature)
            middleState = evaluateBalance(middleTemperature)
            middleResidual = middleState["residual"]
            finalState = middleState

            # print("iteration:", iteration, "Ta:", middleTemperature, "finalBalance:", middleResidual)

            if abs(middleResidual) <= balanceTolerance or abs(upperTemperature - lowerTemperature) <= temperatureTolerance:
                break

            if lowerResidual * middleResidual <= 0.0:
                upperTemperature, upperState, upperResidual = middleTemperature, middleState, middleResidual
            else:
                lowerTemperature, lowerState, lowerResidual = middleTemperature, middleState, middleResidual

    else:
        # use corrected relaxed fixed-point iteration if no valid bracket is found
        relaxationFactor = min(max(float(settings.relaxationFactor), 0.05), 1.0)
        Ta = float(_clampTemperature(previousAirTemperature))

        for iteration in range(maxIterations):
            currentState = evaluateBalance(Ta)
            alphaCanopy = currentState["alphaCanopy"]
            storageCoefficient = currentState["storageCoefficient"]
            denominator = float(_safeDenominator(alphaCanopy + storageCoefficient))

            # solve the rearranged canopy-air balance equation
            trialTa = (
                currentState["QHWeighted"] + qAir
                + alphaCanopy * backgroundTemperature
                + storageCoefficient * previousAirTemperature
            ) / denominator

            trialTa = float(_clampTemperature(trialTa))
            nextTa = float(_clampTemperature(
                relaxationFactor * trialTa + (1.0 - relaxationFactor) * Ta
            ))
            nextState = evaluateBalance(nextTa)
            finalState = nextState

            # print("iteration:", iteration, "Ta:", nextTa, "finalBalance:", nextState["residual"])

            if abs(nextTa - Ta) <= temperatureTolerance and abs(nextState["residual"]) <= balanceTolerance:
                break

            Ta = nextTa

    if finalState is None:
        raise RuntimeError("The canopy-air heat-balance solver did not produce a valid state.")

    # record all heat fluxes using the final internally consistent state
    finalAirTemperature = finalState["Ta"]
    # print(t)
    # print(finalState['alphaGroundAir'],finalState['alphaCanopy'])
    # print(finalState['Tg'])
    # print("Ta", finalAirTemperature)
    # print(finalState["QH"])

    for surfaceName in surfaceNames:
        climate.QHFlux[surfaceName][t] = finalState["QH"][surfaceName]
        climate.QVFlux[surfaceName][t] = - finalState["QV"]
        climate.Qsa[surfaceName][t] = - finalState["Qsa"]

    # print("converged Ta:", finalAirTemperature, "finalBalance:", finalState["residual"])

    # if abs(finalState["residual"]) > balanceTolerance:
    #     print("Warning: canopy-air balance did not reach the requested tolerance. Residual =", finalState["residual"], "W/m2")

    # preserve the original surface-indexed return structure
    Ta = {surfaceName: finalAirTemperature for surfaceName in surfaceNames}
    return {"Tg": finalState["Tg"], "Ta": Ta}

# print("24HeatBalance W/m2: ", sum(result['QS']+result['QL']+result['QV']+result['QA']-result['QGFlux']-result['QVFlux']))
def solveHeatBalance(t, srfHeatFlux, airHeatFlux, climate: ClimateInfo, site: SiteInfo, settings: settingInfo):
    """
    Integrated entry point for the new urban heat-exchange module.
    :param t: hour index in the climate series.
    :param srfHeatFlux: surface heat flux in W/m2, in the form of a dictionary with keys "ground", "land", and "water".
    :param airHeatFlux: air heat flux in W/m2, single value for the entire canopy.
    :param climate: climate info object carrying background weather inputs.
    :param site: site info object carrying area composition and blockage ratio.
    :param settings: settings object carrying the heat-balance configuration, including dt.
    :return: dictionary with the solved canopy temperature and associated heat-balance diagnostics.
    """

    result = approximateTa(t, srfHeatFlux, airHeatFlux, climate, site, settings)

    climate.Ta[t] = weighted(result['Ta'], site.areaFractions)
    climate.Tg[t] = weighted(result['Tg'], site.areaFractions)
    climate.Ta_split['ground'][t] = result['Ta']['ground']
    climate.Ta_split['water'][t] = result['Ta']['water']
    climate.Ta_split['land'][t] = result['Ta']['land']
    climate.Tg_split['ground'][t] = result['Tg']['ground']
    climate.Tg_split['water'][t] = result['Tg']['water']
    climate.Tg_split['land'][t] = result['Tg']['land']
    return result
