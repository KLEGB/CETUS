! Office Building Schedule
! Based on: GB50189-2015 Appendix D (Tables D.0.9-5,6,9,3)
! OccupantDensity unit: person/m²
! OccupantHeatGain unit: W/person (sedentary office work)
! EquipmentHeatGain unit: W/m²
! LightingHeatGain unit: W/m²
!
OFF_OccDens_Weekday,Daily,0,0,0,0,0,0,0,0.004744851,0.010360833,0.020069736,0.02208563,0.023604831,0.022637794,0.022011655,0.02369134,0.023896753,0.024214349,0.022839086,0.015183536,0.010359485,0.00723949,0.004751162,0,0
OFF_OccDens_Weekend,Daily,0,0,0,0,0,0,0,0.001594965,0.002730803,0.003901589,0.00413796,0.004682595,0.004628488,0.004197593,0.005032734,0.005350872,0.005738424,0.005040981,0.002870198,0.002054202,0.001275362,0.000781089,0,0
OFF_OccHeat_AllDay,Daily,75.0,75.0,75.0,75.0,75.0,75.0,75.0,75.0,75.0,75.0,75.0,75.0,75.0,75.0,75.0,75.0,75.0,75.0,75.0,75.0,75.0,75.0,75.0,75.0
OFF_EquipHeat_Weekday,Daily,3.0,3.0,3.0,3.0,3.0,3.0,3.0,4.5,7.5,13.5,13.5,13.5,10.5,10.5,13.5,13.5,13.5,9.0,6.0,4.5,3.0,3.0,3.0,3.0
OFF_EquipHeat_Weekend,Daily,1.5,1.5,1.5,1.5,1.5,1.5,1.5,2.25,2.25,2.25,2.25,2.25,2.25,2.25,2.25,2.25,1.5,1.5,1.5,1.5,1.5,1.5,1.5,1.5
OFF_LightHeat_Weekday,Daily,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.8,4.0,8.0,8.0,8.0,6.4,6.4,8.0,8.0,8.0,4.0,1.6,0.8,0.0,0.0,0.0,0.0
OFF_LightHeat_Weekend,Daily,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.8,0.8,0.8,0.8,0.8,0.8,0.8,0.8,0.8,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0
!
OFF_OccDens_Weekly,Weekly,OFF_OccDens_Weekday,OFF_OccDens_Weekday,OFF_OccDens_Weekday,OFF_OccDens_Weekday,OFF_OccDens_Weekday,OFF_OccDens_Weekend,OFF_OccDens_Weekend
OFF_OccHeat_Weekly,Weekly,OFF_OccHeat_AllDay,OFF_OccHeat_AllDay,OFF_OccHeat_AllDay,OFF_OccHeat_AllDay,OFF_OccHeat_AllDay,OFF_OccHeat_AllDay,OFF_OccHeat_AllDay
OFF_EquipHeat_Weekly,Weekly,OFF_EquipHeat_Weekday,OFF_EquipHeat_Weekday,OFF_EquipHeat_Weekday,OFF_EquipHeat_Weekday,OFF_EquipHeat_Weekday,OFF_EquipHeat_Weekend,OFF_EquipHeat_Weekend
OFF_LightHeat_Weekly,Weekly,OFF_LightHeat_Weekday,OFF_LightHeat_Weekday,OFF_LightHeat_Weekday,OFF_LightHeat_Weekday,OFF_LightHeat_Weekday,OFF_LightHeat_Weekend,OFF_LightHeat_Weekend
