! Residential Building Schedule
! Based on: JGJ26-2018, JGJ134-2010, GB50189-2015 Appendix D
! OccupantDensity unit: person/m²
! OccupantHeatGain unit: W/person (sensible heat, light anthropic)
! EquipmentHeatGain unit: W/m²
! LightingHeatGain unit: W/m²
!
! Weekday schedule: residents mostly at home in morning and evening
! Weekend schedule: residents at home most of the day
!
RES_OccDens_Weekday,Daily,0.030691271,0.036612491,0.03850303,0.036946524,0.031589072,0.022015414,0.011964516,0.002231778,0,0,0,0,0,0,0,0.003612715,0.011424515,0.016766337,0.028416479,0.030211227,0.032947763,0.033329325,0.033196535,0.034548338
RES_OccDens_Weekend,Daily,0.037828914,0.045383798,0.046223106,0.044906782,0.041304343,0.037689849,0.031670492,0.022366318,0.012228194,0.002444774,0,0,0,0.003696881,0.011680952,0.017325238,0.029176182,0.031233416,0.036437113,0.037519227,0.039721503,0.043868935,0.041275382,0.04445834
RES_OccHeat_AllDay,Daily,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0
RES_EquipHeat_Weekday,Daily,0.86,0.64,0.64,0.64,0.64,1.07,2.15,2.58,1.29,0.86,0.86,0.86,1.29,0.86,0.86,0.86,1.29,2.15,3.01,3.44,3.01,2.15,1.29,0.86
RES_EquipHeat_Weekend,Daily,0.86,0.64,0.64,0.64,0.64,0.86,1.72,2.58,3.01,2.79,2.58,2.58,3.01,2.58,2.37,2.37,2.58,2.79,3.22,3.44,3.01,2.37,1.5,0.86
RES_LightHeat_Weekday,Daily,0.25,0.25,0.25,0.25,0.25,0.5,1.5,1.0,0.25,0.25,0.25,0.25,0.25,0.25,0.25,0.25,0.5,1.5,3.0,4.0,3.5,2.5,1.0,0.5
RES_LightHeat_Weekend,Daily,0.25,0.25,0.25,0.25,0.25,0.25,0.75,1.5,2.0,1.75,1.5,1.25,1.25,1.25,1.25,1.25,1.5,2.0,3.0,4.0,3.5,2.75,1.5,0.5
!
RES_OccDens_Weekly,Weekly,RES_OccDens_Weekday,RES_OccDens_Weekday,RES_OccDens_Weekday,RES_OccDens_Weekday,RES_OccDens_Weekday,RES_OccDens_Weekend,RES_OccDens_Weekend
RES_OccHeat_Weekly,Weekly,RES_OccHeat_AllDay,RES_OccHeat_AllDay,RES_OccHeat_AllDay,RES_OccHeat_AllDay,RES_OccHeat_AllDay,RES_OccHeat_AllDay,RES_OccHeat_AllDay
RES_EquipHeat_Weekly,Weekly,RES_EquipHeat_Weekday,RES_EquipHeat_Weekday,RES_EquipHeat_Weekday,RES_EquipHeat_Weekday,RES_EquipHeat_Weekday,RES_EquipHeat_Weekend,RES_EquipHeat_Weekend
RES_LightHeat_Weekly,Weekly,RES_LightHeat_Weekday,RES_LightHeat_Weekday,RES_LightHeat_Weekday,RES_LightHeat_Weekday,RES_LightHeat_Weekday,RES_LightHeat_Weekend,RES_LightHeat_Weekend
