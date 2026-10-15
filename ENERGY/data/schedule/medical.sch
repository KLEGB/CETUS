! medical Building Schedule
! Based on: GB50189-2015 Appendix D (Table D.0.9-6 教学楼)
! OccupantDensity unit: person/m²
! OccupantHeatGain unit: W/person (sedentary classroom anthropic)
! EquipmentHeatGain unit: W/m²
! LightingHeatGain unit: W/m²
!
MED_OccDens_Weekday,Daily,0,0,0,0,0,0,0,0.004744851,0.010360833,0.020069736,0.02208563,0.023604831,0.022637794,0.022011655,0.02369134,0.023896753,0.024214349,0.022839086,0.015183536,0.010359485,0.00723949,0.004751162,0,0
MED_OccDens_Weekend,Daily,0,0,0,0,0,0,0,0.001594965,0.002730803,0.003901589,0.00413796,0.004682595,0.004628488,0.004197593,0.005032734,0.005350872,0.005738424,0.005040981,0.002870198,0.002054202,0.001275362,0.000781089,0,0
MED_OccHeat_AllDay,Daily,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0,70.0
MED_EquipHeat_Weekday,Daily,0.5,0.5,0.5,0.5,0.5,0.5,0.5,1.0,2.0,4.0,4.0,4.0,3.0,3.0,4.0,4.0,4.0,1.5,0.75,0.5,0.5,0.5,0.5,0.5
MED_EquipHeat_Weekend,Daily,0.25,0.25,0.25,0.25,0.25,0.25,0.25,0.25,0.25,0.25,0.25,0.25,0.25,0.25,0.25,0.25,0.25,0.25,0.25,0.25,0.25,0.25,0.25,0.25
MED_LightHeat_Weekday,Daily,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.8,4.0,8.0,8.0,8.0,6.4,6.4,8.0,8.0,8.0,2.4,0.8,0.0,0.0,0.0,0.0,0.0
MED_LightHeat_Weekend,Daily,0.4,0.4,0.4,0.4,0.4,0.4,0.4,0.4,0.4,0.4,0.4,0.4,0.4,0.4,0.4,0.4,0.4,0.4,0.4,0.4,0.4,0.4,0.4,0.4
!
MED_OccDens_Weekly,Weekly,MED_OccDens_Weekday,MED_OccDens_Weekday,MED_OccDens_Weekday,MED_OccDens_Weekday,MED_OccDens_Weekday,MED_OccDens_Weekend,MED_OccDens_Weekend
MED_OccHeat_Weekly,Weekly,MED_OccHeat_AllDay,MED_OccHeat_AllDay,MED_OccHeat_AllDay,MED_OccHeat_AllDay,MED_OccHeat_AllDay,MED_OccHeat_AllDay,MED_OccHeat_AllDay
MED_EquipHeat_Weekly,Weekly,MED_EquipHeat_Weekday,MED_EquipHeat_Weekday,MED_EquipHeat_Weekday,MED_EquipHeat_Weekday,MED_EquipHeat_Weekday,MED_EquipHeat_Weekend,MED_EquipHeat_Weekend
MED_LightHeat_Weekly,Weekly,MED_LightHeat_Weekday,MED_LightHeat_Weekday,MED_LightHeat_Weekday,MED_LightHeat_Weekday,MED_LightHeat_Weekday,MED_LightHeat_Weekend,MED_LightHeat_Weekend
