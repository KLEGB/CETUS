# ENERGY Neighborhood Workflow

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

先把 `list[ClimateInfo]` 拼接成连续 Moosas 气象 CSV，然后逐栋建筑调用 `BuildingInstance.simulation()`。每栋建筑只调用一次 MoosasEnergy，可支持 24 小时、多日或全年 8760 小时。结果按 `GFA` 汇总成区域单位用地净负荷，并按 24 小时分块写回 `ClimateInfo.coolingLoad`。

### `writeEPW(epwPath)`

读取原始 EPW 的干球温度、相对湿度、大气压和露点，先由原始 `Ta/Rh/Pa` 计算含湿量，再用 UHI 更新后的 `ClimateInfo.Ta` 替换请求窗口内的 EPW 干球温度，并反算新的相对湿度和露点后输出新 EPW。

## JSON

`BuildingInstance` 和 `Neighborhood` 都支持：

- `saveJSON(path)`
- `loadJSON(path)`
- `toDict() / fromDict()`

旧的 `to_dict() / from_dict()` 仍保留为兼容包装。
