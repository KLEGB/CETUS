# ENERGY Neighborhood Workflow

## 新版 JSON 显热内核（2026-10-07）

最新源文件是 `bin/MoosasEnergy-new.go`，协议说明为 `bin/Energy-Readme.md`。
`bin/MoosasEnergy.go`、`bin/README.md` 属于旧 `.i/.o` 模型。
`bin/MoosasEnergy.exe` 与 `bin/MoosasEnergy` 已替换为新版 JSON 引擎（Windows/Linux amd64）。
旧执行文件保存为 `MoosasEnergy-legacy.exe`、`MoosasEnergy-legacy`，旧 Python 接口使用这些兼容文件。
新版通过独立入口接入：

```python
# ng 已读取建筑、climate，并设置建筑热工参数和作息；可先运行 RAD/UHI。
results = ng.energySimulationSteady(output_dir="outputs/energy_steady")
```

默认使用已编译的 `MoosasEnergy.exe`（Linux 为 `MoosasEnergy`）。
文件缺失或源文件更新时自动用 Go 编译 `MoosasEnergy-new.go`，此时需要 Go 1.20+。
也可传入 `executable_path` 指向已编译的新版执行文件。
旧 `energySimulation()` 保持原接口。新版每栋楼按单个混合热区、每自然日一次调用，
保留整栋外墙/窗面积、屋面面积和地接面积；结果覆盖 climate 的完整日窗口。

| 新输入 | Urban 映射或近似 |
| --- | --- |
| `hour_ending` | `ClimateInfo.HOY + 1`；必须完整自然日、按时间递增 |
| `air_c`、`boundary_c` | `Ta`、`[Ta, Ta, Tg, Ta]`；Tg 是地接边界的近似，UHI 路面温度不等于真实地下温度 |
| `sky_ir_w_m2` | `sigma * (SkyTemperature + 273.15)^4`；EPW 导入时天空温度来自水平红外辐射；无有效天空温度用 Ta 近似 |
| `irradiance_w_m2` | 有 RAD 时取 N/W/S/E 等权平均和 T 屋面辐射；无 RAD 时屋面用 RadTotal，立面用 `0.5*DHI + 0.25*(GHI-DHI) + 0.1*GHI`；地接面和玻璃外表面项为 0 |
| `solar_transmitted_w` | 窗/天窗入射辐射乘实际面积与 SHGC 一次；缺少玻璃光学分项时，以有效透射得热近似总太阳得热；内核还保留其自身 3.5% 逸出假设 |
| `solar_absorbed_inward_w` | 默认 0；可用 `glass_inward_fraction` 将上述总太阳得热拆出一部分，避免重复计热 |
| `internal_w` | GFA ×（人数密度 × 人员显热 + 设备强度 + 照明强度），按实际 HOY 解析已有作息；数值输入表示恒定强度 |
| `infiltration_ach` | 工作时段 InfiltrationACH、其余 NightVentilationACH，加 `人数密度*人均新风量/层高` 后取日均；起止相同时按全天使用 |
| `density_kg_m3` | Pressure、Ta、HumidityRatio 推算湿空气密度后取日均，不重复做海拔修正 |
| `hi`、`ho` | hi 近似 3；ho 近似 `5.8 + 4.1*日均风速 + 5.13`，含固定辐射换热部分；可覆盖 |
| `resistance` | 优先使用显式覆盖值；否则假设 U 的综合参考膜热阻：内墙/窗 .13、屋面 .10、地板 .17，外膜 .04，按 `max(0, 1/U-Rsi-Rse)` 近似，单位 m²K/W；不使用当前对流 hi 反推 |
| `sky_view`、`absorptance` | 墙/窗用 site.SVF/2，屋面用 1、地板 0；墙/屋面吸收率借用 setting.AbsoRoof；玻璃外表面吸收项为 0 |
| 温控、COP | 现有温控作息与 CoolingEER/HeatingEER；必须供暖点低于制冷点且 COP>0 |
| `parameters`、`redistribution` | 膜倍率 1、太阳地板份额 .642、内热辐射比例 .6 为显式假设；没有随包标定参数，默认关闭辐射重分配，不冒用 benchmark 标定值 |

`options` 支持覆盖模型假设，例如：

```python
results = ng.energySimulationSteady(
    output_dir="outputs/energy_steady",
    options={
        "surfaces": {"wall": {"resistance": 2.0, "absorptance": 0.6}},
        "internal_radiant_fraction": 0.6,
        "glass_inward_fraction": 0.0,
    },
)
```

支持的其余 options：`stage`、`parameters`、`redistribution`、`infiltration_ach`、
`density_kg_m3`、`cp_j_kg_k`；表面可覆盖协议中的数值字段，地接面天空视角固定为 0。
单栋单日也可通过 `ENERGY.build_steady_input()` 和 `ENERGY.run_steady_day()` 调用。

每栋每天保存 `input.json`、`mapping.json`、`output.json`，根目录保存 `summary.json`。
新版返回整栋逐时 `cooling_kwh`、`heating_kwh`（热负荷）、`hvac_electricity_kwh`，
以及 `cooling_w_m2` 和 `condenser_rejection_wh = 冷负荷Wh*(1+1/COP)`。
冷凝器排热保留为诊断值。实际 UHI 回写采用整栋逐时有符号净热源：

`ACWasteHeat = ACLoad + electricity_wh - air_exchange_wh - contact_transfer_wh`

- `ACLoad = 1000 * (cooling_kwh - heating_kwh)`：制冷为正、供暖为负，死区为零。
- `electricity_wh = 1000 * hvac_electricity_kwh`：电功为正，包含制冷和供暖电功。
- `air_exchange_wh`：Go 的 `balance.infiltration_w`，含等效合并的新风与渗透显热。
- `contact_transfer_wh`：Go 新增的四面 `balance.contact_transfer_w` 之和，含墙、屋面、地板、窗的温差传热。

交换项以室外进入建筑为正，负值代表建筑向外散热；所有项在一小时步长下由 W 数值转为 Wh，不取绝对值、不截断。
R3/R4/R5 的接触项为 `A/(Rsolid+1/ho)*(boundary_c-surface_c)`，其中 ho 已乘 exterior_film；
R2 为 `A*U*(boundary_c-zone_air_c)`。它沿用内核的等效外膜近似，排除太阳/天空辐射形成的外侧温度修正。
不能用 `surface_to_air_w` 或整个 `boundary_to_surface_w` 代替该扣除项，因为它们含辐射得热。

每次全部建筑成功后，覆盖 `building.ACLoad/ACWasteHeat/energyHOY`，设置 `energyModel="steady"`，
将四项 Wh 保存在 `energyFeedback`，清空旧 `loadDecompose`。
`building.coolingLoad/heatingLoad/load` 保持旧接口的耗电强度 W/m² 口径，load 为制冷减供暖。
JSON 保存与恢复包含模型标记及反馈分项，`updateACResults()` 按标记重算，不再套用旧分解。
切换回旧模型时须全建筑重算，不能混合两套结果进行部分汇总。

`climate.ACLoad/ACWasteHeat` 是当日所有建筑逐时总和；
`climate.coolingLoad = climate.ACWasteHeat / site.TotalArea`，单位 Wh/m²_site，在一小时步长下等于 W/m²。
UHI 直接读取它并另加交通热，不再除 COP、不叠加经验建筑废热。重复运行覆盖上一轮反馈。

```python
# 分开调用：ENERGY 回写后，UHI 使用该轮净热源。
ng.energySimulationSteady(output_dir="outputs/energy_steady")
uhi_result = ng.uhiSimulation()

# 或一次调用：先以当前 climate 计算 ENERGY，再运行 UHI。
uhi_result = ng.uhiSimulation(
    energy_model="steady",
    energy_kwargs={"output_dir": "outputs/energy_steady"},
)
```

每次是一次显式 ENERGY→UHI 更新。再次调用上述组合会以更新后的 Ta/Tg 计算下一轮能源反馈；
这里不自动判定耦合收敛。UHI 的 preheat 是其自身预热，不会在内部重跑 ENERGY。
新版不含潜热、直接照明/设备电耗和完整 HVAC 启停模型，不能与旧版总能耗等同。
Go 要求四类表面面积都大于 0；无窗等不支持的几何明确报错，不填虚构面积。

`ENERGY` 模块以 `Neighborhood` 作为建筑群工作流入口，`BuildingInstance` 作为单栋建筑对象，`Schedule` 作为 Moosas `.sch` 文件的结构化管理器。

## Main Objects

### `Schedule`

`Schedule` 负责解析和复原 MoosasEnergy 的 schedule 文本，支持三类 schedule：

- `Daily`: 24 个典型日数值。
- `Weekly`: 7 个 Daily schedule 引用。
- `Hourly`: 8760 个逐时数值。

常用方法：

- `Schedule.fromText(text)`: 从 `.sch` 字符串创建对象。
- `schedule.mergeText(text)`: 合并另一段 `.sch` 字符串。
- `schedule.toText()`: 复原为 MoosasEnergy 可读取的 `.sch` 文本。
- `schedule.scaleName(name, factor)`: 对一个 schedule 做整体缩放。

### `BuildingInstance`

`BuildingInstance` 管理单栋建筑的几何、热工、schedule 和能耗结果。

常用方法：

- `fromAOI(geometry, height, StoryHeight=4.0, wwr=0.5)`: 从 shapely footprint 和建筑高度更新几何参数。
- `fromArchetype(climate_zone, building_type, standard="GB55015-2021", rng=None, **kwargs)`: 从 `ENERGY/data/archetype/mean.csv` 和 `std.csv` 抽样更新热工参数。
- `includeSolarSchedule(sch)`: 合并 RAD 生成的太阳辐射 schedule。
- `writeSchedule()`: 输出完整 `.sch` 文本。
- `toStr()`: 输出 MoosasEnergy 三行 `.i` 文件文本，顺序为屋顶层、中间层、地面层。
- `simulation(location, year=False, month=True, daily=False, hourly=False, weather_path=None)`: 使用经典 Moosas 气象 CSV 做单栋模拟。

`BuildingInstance.load` 的单位为 `W/m2`，统计口径为代表楼层单位建筑面积净负荷：

- 楼层数 `<= 1`: `(顶层 + 底层) / 2`
- 楼层数 `> 1`: `(顶层 + 中间层 * (multipler - 2) + 底层) / multipler`

其中 `multipler = height / StoryHeight`，`GFA = FloorArea * multipler`。

### `Neighborhood`

`Neighborhood` 管理建筑群、多日气象、EPW、RAD、UHI 和能耗链路。

核心属性：

- `buildings`: `list[BuildingInstance]`
- `site`: `SiteInfo`
- `climate`: `list[ClimateInfo]`，每个 `ClimateInfo` 仍固定为 24 小时。
- `location`: `EpwLocation`
- `setting`: `settingInfo`
- `_start_hoy/_end_hoy`: 用户请求窗口，左闭右开。
- `_expanded_start_hoy/_expanded_end_hoy`: 拉齐到完整自然日后的内部窗口。
- `_shp/_epw/_mtx/_rad`: 中间文件路径。

## Typical Workflow

```python
from ENERGY import Neighborhood

shp = r"E:\PycharmProjects\MoosasUrban\workplace\data\all_stations\packed\1000m\LCZ1\shp\59109_2166_LCZ1_4B.shp"
epw = r"E:\PycharmProjects\MoosasUrban\workplace\epw\CHN_Guangdong.Guangzhou.592870_CSWD.epw"

ng = Neighborhood()
ng.readSHP(shp)
ng.readEPW(epw, start_hoy=0, end_hoy=8760)
ng.radSimulation(sample=0.3)
ng.sampleInstance(seed=42)
# 如果已手动修改建筑 schedule 或 archetype，可显式重新接入 RAD:
# ng.processRad()
ng.uhiSimulation()
energy_results = ng.energySimulation()
ng.saveJSON(r"E:\PycharmProjects\MoosasUrban\outputs\energy\neighborhood.json")
ng.writeEPW(r"E:\PycharmProjects\MoosasUrban\outputs\energy\uhi_updated.epw")
```

也可以使用默认初始化链路：

```python
ng = Neighborhood.fromFile(
    shpFile=shp,
    epwFile=epw,
    start_hoy=0,
    end_hpy=8759,
    fvc=None,
    fwc=None,
)
ng.uhiSimulation()
ng.energySimulation()
```

## Method Notes

### `fromFile(shpFile, epwFile, start_hoy=0, fvc=None, fwc=None, end_hpy=8759)`

从 SHP 和 EPW 创建一个完整可模拟的 `Neighborhood`。内部执行顺序为 `readEPW()`、`readSHP()`、可选 `SiteInfo` 覆盖、`radSimulation(sample=0.3)`、`sampleInstance()`。`end_hpy` 按包含上界处理，所以默认 `0..8759` 会传入当前半开区间 `[0, 8760)`。

### `readSHP(shp_path, height_field="height")`

该方法不直接基于 shp 计算 `SiteInfo`，而是先调用 `SITE.transform.shp_to_tif()` 生成带高度的 tif，再使用 `SiteInfo.fromTIF()` 更新场地信息。建筑几何仍按 shp 行顺序读取，逐栋调用 `BuildingInstance.fromAOI()`。

### `readEPW(epwFile, start_hoy=None, end_hoy=None, date="08-20")`

`readEPW()` 接受单日 `date="mm-dd"` 或任意 `[start_hoy, end_hoy)`。当窗口不足整日时，会扩展到完整自然日，然后拆成多个 24 小时 `ClimateInfo`。

### `radSimulation(epwFile=None, radFile=None, sample=0.3)`

如果传入 `radFile`，直接复用已有 `rad.npz`。否则先从 EPW 构建 `mtx.npy`，再计算全年 `rad.npz`。当前 RAD 计算逻辑是每栋建筑一次 MoosasRad 调用：该栋建筑的所有射线作为一批，场景包含建筑本体和候选邻体。

### `processRad(radFile=None)`

显式把全年 `rad.npz` 解算为每栋建筑的 solar schedule，并合并进 `BuildingInstance.schedule`。如果传入 `radFile`，会先记录该文件路径，跳过前置辐射计算复用已有结果。

### `sampleInstance(...)`

根据六类建筑比例抽样分配 archetype。比例会按总和归一化，允许总和不等于 `1.0`。该方法要求已完成 `readEPW()` 和 `radSimulation()`，因为 archetype 抽样需要 climate zone，solar schedule 需要 `_rad`。

### `uhiSimulation(**kwargs)`

更新 `settingInfo` 后调用 `UHI.calculate(self.climate, site_series, ...)`。UHI 结果不额外保存在 `Neighborhood`，而是直接回写每个 `ClimateInfo` 的 `Tb/Ta/Tg`。

### `energySimulation(...)`

先把 `list[ClimateInfo]` 拼接成连续 Moosas 气象 CSV，然后逐栋建筑调用 `BuildingInstance.simulation()`。每栋建筑只调用一次 MoosasEnergy，可支持 24 小时、多日或全年 8760 小时。

Python 模拟接口已移除 `decompose` 参数，始终获取并保存四项分解。引擎命令固定传入 `-dc 1`，兼容随项目分发的可执行文件。`loadDecompose` 和返回值中的 `decompose` 仍保存分项结果，单位为 kWh/m²_GFA。

每项分解乘 `1000 × building.GFA` 后得到整栋建筑逐时 Wh，并计算：

- `building.ACLoad = Lsol + Lcon + Lvent + Lint`。
- `building.ACWasteHeat = Lsol + Lint + ACLoad / building.CoolingEER`。
- `climate.ACLoad` 和 `climate.ACWasteHeat` 分别为所有建筑对应指标的逐时总和，单位 Wh，每个 climate 保存当日 24 项。
- `climate.coolingLoad = climate.ACWasteHeat / site.TotalArea`，单位 Wh/m²_site。当前一步为 1 小时，与该小时平均 W/m² 数值相同，直接供 UHI 使用。

ACLoad 保留分项代数和及其负值，不施加截断或 0.75 系数。现有 `building.coolingLoad/heatingLoad/load` 继续保留引擎的耗电强度口径，不能作为 ACLoad 再除 EER。采暖季分项沿用引擎原有符号，本轮未新增采暖废热模型；AC 指标的制冷解释主要用于制冷季。

每轮覆盖 climate 上一轮结果，不累积历史。选择 `building_indices` 部分重算时，其余建筑必须已有同一 HOY 时段的完整分解结果，否则拒绝汇总；完整耦合迭代应重算所有建筑。

`site.GFA = sum(building.GFA)`，`site.plotRatio = site.GFA / site.TotalArea`。读取建筑后先同步面积，再依据 LCZ 加载参数；恢复 JSON、模拟前和保存时也同步。独立 SiteInfo 输入仅提供容积率时，通过容积率与场地面积推算 GFA；显式提供 GFA 时以 GFA 为准。修改 SiteInfo 的 GFA 或 TotalArea 会自动更新 plotRatio。

### `writeEPW(epwPath)`

读取原始 EPW 的干球温度、相对湿度、大气压和露点，先由原始 `Ta/Rh/Pa` 计算含湿量，再用 UHI 更新后的 `ClimateInfo.Ta` 替换请求窗口内的 EPW 干球温度，并反算新的相对湿度和露点后输出新 EPW。

## JSON

`BuildingInstance` 和 `Neighborhood` 都支持：

- `saveJSON(path)`
- `loadJSON(path)`
- `toDict() / fromDict()`

旧的 `to_dict() / from_dict()` 仍保留为兼容包装。
