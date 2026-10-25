# CETUS 1.9：光谱近似、无几何采光与照明电热耦合

版本：1.9.0。基线：交付的 `CETUS_V8.4.zip`。Energy Go 热平衡与 CES3 布局保持不变。

## 物理计算链

RAD 输出入射总短波与一次反射短波，均为 W/m²。长波只用于热平衡，不参与照度。若分量分别输入，则必须满足 `primary + reflected = total`；反射不得在总量上再叠加。

对默认同谱近似，`I_vis = f_vis I_SW`，`I_NIR = f_NIR I_SW`，剩余份额为其他短波。份额必须非负且和为 1。默认 `(0.43, 0.52, 0.05)` 是可修改先验，不是从宽波段辐射反演的实测光谱。

窗外照度 `E_window = K_vis I_vis`。默认 `K_vis=250 lm/W_visible`；这一系数作用于可见波段能量，不能直接用来乘总太阳短波。

玻璃的近似太阳透射率：

`tau_solar = f_vis tau_vis + f_NIR tau_NIR + f_other tau_other`。

用户需同时提供 `tau_vis` 和 `tau_NIR`；不能以 SHGC 代替。这里的 `tau_vis` 是在所声明可见波段与光效假设下的等效灰带透射率。不同光谱下的光度透射率与能量透射率未必严格相等，需用匹配资料校准。

光谱材料定义在关闭照明控制时仍生效。这样 on/off 对照使用同一玻璃。若未提供光谱材料、也未开启控制，继续使用 V8.4 的 `WindowTransmittance`。光谱输入不能与同一窗面的 `surfaces.*.transmittance` 覆盖同时提供。

## 按面积自动分配

不创建室内几何或额外温度节点。

- `FloorArea` 为典型楼层面积，`GFA` 为总面积。按整层和剩余顶层划分，面积严格守恒。
- 每层周边面积比例为 `PerimeterZoneArea / FloorArea`。超过典型楼面面积时拒绝输入。
- 总立面窗面积按各层面积占比分配。默认周边区获得立面窗光，核心区立面日光为零。
- 工作面平均照度 `E_per = E_window tau_vis A_window u_facade / A_perimeter`。
- 天窗仅分配给顶层。无分隔参数时，天窗光按顶层面积形成平均照度；有房间参数时，仅服务显式分配了天窗的房间。
- `rooms_by_floor` 允许每层输入房间面积、是否有窗、窗面积及可选房间窗面辐照度，不要求多边形。窗区面积受房间面积上限约束；无窗房间保留照明。
- 若房间只有面积、没有窗归属，无法识别真实有窗房间，采用周边/核心区代理并记录说明；未说明的楼面区域保守保留照明。

`u_facade=0.22`、`u_skylight=0.35` 为尚未标定的等效利用系数。室内工作面结果不是逐点照度、眩光或采光合规结论。立面方向平均、隔断、直射斑块和窗帘行为均会影响准确性。

## 300 lx 开关与日程

`calls_for_light = (E_daylight < threshold_lux)`；恰好达到阈值则关闭受控照明。默认阈值 300 lx。

`P_light = P_baseline [(1-c) + c calls_for_light]`，其中 `c` 是受控功率份额。基线是既有 `LightingHeatGain` 的 W/m² 日程乘 GFA，不能再次乘 LPD。原日程不修改，因此重复调用不会累乘节能。基线关闭时不会因日光不足而强制开启。

`P_internal_new = P_internal_old - P_light_baseline + P_light_new`。

照明电功不除 COP；照明得热不再直接加到 UHI 空气。透窗太阳能仍只由热通道记账；照度通道不增加第二份太阳热。首版沿用原总内热源辐射/对流分配，全部照明电功进入热区。

## 公共接口

CETUS：`n.radSimulation(energy_options=options, ...)`，随后 `simulate_coupled(n, options=options, ...)`。

```python
options = {'daylighting': {
    'enabled': True,
    'visible_transmittance': 0.7,
    'near_infrared_transmittance': 0.2,
    'threshold_lux': 300,
    'controlled_fraction': 1.0,
}}
```

独立 Energy：`from ENERGY import simulate_surface`；调用 `simulate_surface(building, climates, irradiance_by_day=..., options=options)`。使用标量参数创建 `BuildingInstance`，不调用 `fromAOI()`。`ExteriorWallArea/ExteriorWindowArea` 为典型楼层面积，屋面、天窗和地面面积为全建筑口径。完整例见 `test/examples/run_v1_9_standalone.py`。

可单独调用 `estimate_daylighting` 处理任意长度短波序列。`facade_shortwave_w_m2` 与可选 `facade_reflected_w_m2` 在这个低层函数中是两个可加分量。上层 `build_surface_input` 接收总量及带 primary/reflected 后缀的可选分量，且核对闭合。

`visible_reflected_fraction` 只调整采光端的反射可见份额，热端仍使用均匀灰带综合透射率；它不是完整的分波段外部反射—热传输模型。默认不设置此项。若需要准确的光谱反射热增益，应另行扩展热光谱通道。

## 输出与缓存

原 `electricity_w` 仍表示 HVAC。新增每栋逐时字段：`baseline_lighting_w`、`lighting_electricity_w`、`avoided_lighting_w`、`lighting_schedule_ratio`、`hvac_plus_lighting_electricity_w`。汇总为对应 kWh。仅统计 HVAC 与照明，不宣称建筑全部终端用电。

耦合目录提供 `hourly.csv`、`building_hourly.json`、`daylighting_hourly.csv`、`audit.json`、`state.json`。照度诊断含 HOY、建筑编号、分区面积、自然光照度和开灯需求。

照明在每个天气日预计算，温度候选迭代不重复计算。新控制配置写入重启签名；改变控制配置不能复用旧热状态。改变阈值但不改变材料可复用辐射网络；显式载入时将新的控制配置写回网络元数据。

## 验证边界

ASHRAE 140 内热源比较检验热响应，不直接验证窗面短波到工作面照度的等效系数。全年四个算例对比与历史四例 July 回归见测试报告。无窗、夜间、部分受控、无几何参数、分隔房间、重复调用、重启、JSON/二进制和解析电热平衡已测试。

照度精度仍需独立 Radiance/实测标定。小时平均光照经过开关函数也不能替代分钟级灯光行为；所有节能数字取决于气象、玻璃、利用系数、阈值、基线日程和控制覆盖率。
