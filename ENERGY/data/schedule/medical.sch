! medical Building Schedule
! Based on: GB50189-2015 Appendix D (Table D.0.9-6 教学楼)
! OccupantDensity unit: person/m²
! OccupantHeatGain unit: W/person (sedentary classroom activity)
! EquipmentHeatGain unit: W/m²
! LightingHeatGain unit: W/m²
!
MED_OccDens_Weekday,Daily,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0167,0.0835,0.1587,0.1587,0.1587,0.1336,0.1336,0.1587,0.1587,0.1587,0.0501,0.0167,0.0,0.0,0.0,0.0,0.0
MED_OccDens_Weekend,Daily,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0
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
