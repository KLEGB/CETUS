! Commercial Building (Mall/Retail) Schedule
! Based on: GB50189-2015 Appendix D (Table D.0.9-6 商场建筑)
! OccupantDensity unit: person/m²
! OccupantHeatGain unit: W/person (light walking/shopping anthropic)
! EquipmentHeatGain unit: W/m²
! LightingHeatGain unit: W/m²
!
COM_OccDens_Weekday,Daily,0,0,0,0,0,0,0,0.004744851,0.010360833,0.020069736,0.02208563,0.023604831,0.022637794,0.022011655,0.02369134,0.023896753,0.024214349,0.022839086,0.015183536,0.010359485,0.00723949,0.004751162,0,0
COM_OccDens_Weekend,Daily,0,0,0,0,0,0,0,0.001594965,0.002730803,0.003901589,0.00413796,0.004682595,0.004628488,0.004197593,0.005032734,0.005350872,0.005738424,0.005040981,0.002870198,0.002054202,0.001275362,0.000781089,0,0
COM_OccHeat_AllDay,Daily,100.0,100.0,100.0,100.0,100.0,100.0,100.0,100.0,100.0,100.0,100.0,100.0,100.0,100.0,100.0,100.0,100.0,100.0,100.0,100.0,100.0,100.0,100.0,100.0
COM_EquipHeat_Weekday,Daily,3.9,3.9,3.9,3.9,3.9,3.9,3.9,5.2,7.8,10.4,11.7,11.7,11.7,11.7,11.7,11.7,11.7,11.7,11.7,11.7,11.05,9.1,6.5,4.55
COM_EquipHeat_Weekend,Daily,3.9,3.9,3.9,3.9,3.9,3.9,3.9,5.2,7.8,11.05,12.35,12.35,12.35,12.35,12.35,12.35,12.35,12.35,12.35,11.7,11.05,9.1,6.5,4.55
COM_LightHeat_Weekday,Daily,0.9,0.9,0.9,0.9,0.9,0.9,0.9,1.8,4.5,8.1,9.0,9.0,9.0,9.0,9.0,9.0,9.0,9.0,9.0,9.0,8.1,6.3,2.7,0.9
COM_LightHeat_Weekend,Daily,0.9,0.9,0.9,0.9,0.9,0.9,0.9,1.8,4.5,8.55,9.0,9.0,9.0,9.0,9.0,9.0,9.0,9.0,9.0,9.0,8.1,6.3,2.7,0.9
!
COM_OccDens_Weekly,Weekly,COM_OccDens_Weekday,COM_OccDens_Weekday,COM_OccDens_Weekday,COM_OccDens_Weekday,COM_OccDens_Weekday,COM_OccDens_Weekend,COM_OccDens_Weekend
COM_OccHeat_Weekly,Weekly,COM_OccHeat_AllDay,COM_OccHeat_AllDay,COM_OccHeat_AllDay,COM_OccHeat_AllDay,COM_OccHeat_AllDay,COM_OccHeat_AllDay,COM_OccHeat_AllDay
COM_EquipHeat_Weekly,Weekly,COM_EquipHeat_Weekday,COM_EquipHeat_Weekday,COM_EquipHeat_Weekday,COM_EquipHeat_Weekday,COM_EquipHeat_Weekday,COM_EquipHeat_Weekend,COM_EquipHeat_Weekend
COM_LightHeat_Weekly,Weekly,COM_LightHeat_Weekday,COM_LightHeat_Weekday,COM_LightHeat_Weekday,COM_LightHeat_Weekday,COM_LightHeat_Weekday,COM_LightHeat_Weekend,COM_LightHeat_Weekend
