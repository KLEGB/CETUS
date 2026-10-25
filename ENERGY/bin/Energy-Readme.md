# V8.4 当前接口

最新接口文档为 [CetusSurfaceEnergy_V8.4_Interface.md](../../docs/CetusSurfaceEnergy_V8.4_Interface.md)。以下保留旧版说明。

# V8.3 接口入口更新

当前 CetusSurfaceEnergy 的权威接口文档为 [CetusSurfaceEnergy_V8.3_Interface.md](../../docs/CetusSurfaceEnergy_V8.3_Interface.md)。下文保留历史接口说明，不代表新版二进制协议。

# MoosasEnergy 夏季稳态传热＋经验时序转换协议 v4

本项目对应的 R5 源文件为 `MoosasEnergy-new.go`；`MoosasEnergy.go` 保留旧 `.i/.o` 源码。原上游说明提到的 examples/ 与 reproduce/parameters.json 未随本项目分发；输入可由 ENERGY.steady.build_steady_input 生成。不要从 benchmark 的200W、COP=1或Denver空气密度直接推成通用参数。

Linux/macOS：`go build -o MoosasEnergy MoosasEnergy-new.go`；Windows：`go build -o MoosasEnergy.exe MoosasEnergy-new.go`，随后 `MoosasEnergy.exe input.json output.json`。成功退出码0，失败通过stderr返回错误和非零退出码。输入输出均为UTF-8 JSON。当前分发二进制为 windows/amd64 和 linux/amd64，CGO_ENABLED=0。

## CETUS 对接映射与未覆盖功能

|原系统量|本协议接入方式|
|---|---|
|空间几何|每热区单独调用，四表面面积与volume显式输入；多区/多建筑汇总由调用方完成|
|墙/屋面/地板/玻璃材料|R5 使用不含膜的 resistance，优先由各材料厚度/导热系数求和；u仅在R2参与传热及R5识别显式绝热|
|只有整体系数U|不能直接用1/U减当前对流hi倒数推算构造R；必须先确定U的参考内外**综合**膜定义|
|Ta、Tg|air_c=Ta；boundary_c按墙、屋面、地板、窗分别给出；是否地接或架空由调用方决定|
|太阳边界与空气温度相同|可设 boundary_c=[Ta,Ta,Tg,Ta] 或实际所需同温度；不意味着外表面短波项为零，不能自动消除城市侧重复计热|
|太阳辐射|入射短波给irradiance；透射与玻璃向内吸收分别给两个solar字段，单位W；不再重复乘SHGC|
|人员/设备/照明|调用方按作息、面积与采光控制合并为internal_w；内部只处理显热|
|新风与渗透|显热可合成与rho匹配的ACH；潜热/焓差未实现，不可默默省略后宣称和原模型同范围|
|末端总电耗|Go只返回HVAC电耗；调用方另加照明设备直接电耗|
|热反馈|现有表面热流不能直接代替旧Lcon_air；与urban侧源项拆分的注意事项见Energy-Validation-Report.md第5节|

当前要求四表面面积都>0，不支持用零面积跳过表面；显式u=0表示该面绝热。没有HVAC启停布尔作息；改变设定点可模拟部分温控变化，但不等价于完整停机/启动模型。不能把本入口称为旧工程所有功能的无损替换。

入口：`go build -o MoosasEnergy MoosasEnergy-new.go`，然后 `./MoosasEnergy input.json output.json`。
Go 1.20+，只使用标准库。该入口是本轮新增的单热区显热计算内核，不兼容旧 `.i/.o` 入口。原工程中的人员/照明/新风作息、建筑空间组织及报表，需要调用方先转换到本协议；不是旧版全部功能的替换文件。

## 计算范围

- R5 首先按完整自然日重分配辐射得热，再逐小时求稳态热平衡；没有 RC/CTF 或热容状态积分，但含经验等效滞后，不能称为纯即时稳态模型。
- 重分配使用当天完整输入，属于非因果离线近似，不能直接用于实时预测；不携带前一天状态，连续阴晴变化和跨日释放未验证。
- 夏季显热负荷；允许同时给出供暖和制冷设定点，死区内输出零 HVAC 负荷，以正确比较七月逐时有符号参考值。
- 单热区、四组表面：不透明外墙、屋面、地板、窗。表面组内使用面积加权边界与辐射。
- 输入已是逐小时量，不处理气象文件、太阳位置或玻璃光学；这些由 `experiment.py` 的可复现预处理提供。CETUS 可以用其自身日照与气象模块准备同名量。
- 显热新风量可与渗透等效合并到 ACH；湿负荷、冷机性能曲线、旧版住宅/公共建筑季节与周末修正未移植。

## JSON 顶层：所有字段必填

|字段|单位/取值|语义|
|---|---|---|
|protocol|`moosas-steady-4`|固定版本标识|
|stage|`R2` / `R3` / `R4` / `R5`|R5：每日重分配＋R3 表面代数网络；其余用于消融对照|
|volume_m3|m³，>0|热区体积|
|infiltration_ach|h⁻¹，≥0|与所给空气密度相匹配的换气次数，禁止重复海拔修正|
|density_kg_m3|kg/m³，>0|空气密度；本试验使用 1.0156|
|cp_j_kg_k|J/(kg·K)，>0|空气定压比热；本试验使用 1015|
|cooling_cop、heating_cop|>0|仅计算电耗，不能改变热负荷|
|internal_radiant_fraction|0–1|内热源辐射比例；本试验 0.6|
|parameters|对象|见下表|
|redistribution|对象|见新增分配字段，全部必填|
|surfaces|4 个对象|固定顺序 wall、roof、floor、window|
|hours|非空数组|R5 只接受完整自然日，每日按 1–24 结束小时递增，可输入七月或完整单日|

未知字段、缺失字段、null、负 U、非法数组长度及不递增时间均报错并返回非零退出码。合法的显式 `u=0` 表示绝热，不会回退为墙体 U 值。

### parameters

|字段|默认值|本轮搜索界限|实际作用|
|---|---:|---:|---|
|interior_convection|1|0.5–2|乘在各表面的室内对流换热系数上|
|exterior_film|1|0.5–2|乘在外表面综合换热系数上，同时影响外膜热阻和太阳/天空边界项|
|solar_floor_fraction|0.642|0.5–0.8|透射太阳辐射的地板吸收份额；其他表面按守恒重新分配|

这是共同参数的研究搜索范围，不是标准给出的容差或通用推荐值。R5 不搜索 solar_floor_fraction，而固定为 0.642。共同参数及适用范围见 parameters.json 与 REPORT.md，不能视为已经独立验证的 CETUS 通用默认值。

### redistribution：新增必填对象

|字段|搜索范围|含义|
|---|---|---|
|window_fraction|0–1|窗透射短波参与全日重新分配的份额|
|opaque_fraction|0–1|各外表面短波参与重新分配的份额，墙与屋面共用|
|delay_hours|0–4 h|相对该通道原始日得热重心的延后量|
|width_hours|1–7 h|高斯分配曲线宽度|
|background_fraction|0–0.5|分配能量中均匀分布到 24 小时的缓慢释放份额|

上述五项和室内/外膜倍率共七项，在四个案例间完全共享。程序不读取 case 编号。实际运行可以关闭 window_fraction/opaque_fraction 进行消融；两者都为零时恢复原始驱动。

对窗透射短波和每个外表面短波分别计算日和 E（W×1h）、原始重心 c=Σ(h*q_h)/Σq_h；高斯中心为 c+delay_hours。高斯在 1–24 小时内归一化，混入 background_fraction/24 后得到权重 w_h。有效驱动为 `(1-f)*q_h+f*E*w_h`，因步长固定 1h，数值单位为 W。每个通道 Σw_h=1。零能量通道仍为零；不跨日混合。

外表面转换后的 irradiance 是**有效得热驱动**，并非那个小时的真实太阳辐照度。夜间的正值表示经验释放份额。由于面积与吸收率不随小时变化，每日外表面吸收能量也守恒。程序不改变气温、天空长波、SHGC/透射率输入、材料、渗透、200W 内热源、玻璃向内吸收得热或温控作息。每日输入得热守恒不等于每日冷负荷守恒：温度差、向外散热及 HVAC 截断会改变冷负荷。

### surfaces 中每个对象

|字段|单位|说明|
|---|---|---|
|name|字符串|wall / roof / floor / window，按此顺序|
|area|m²|必须 >0；零面积表面不在本版适用范围内|
|u|W/(m²·K)|R2 使用空气到空气 U；R3/R4 中仅用于显式识别绝热面|
|resistance|m²·K/W|R3/R4 使用不含内外表面膜的构造热阻；与 u 不能叠加|
|hi|W/(m²·K)|室内**对流**换热系数，基值|
|ho|W/(m²·K)|室外**综合**换热系数，基值|
|sky_view|0–1|天空视角因子|
|absorptance|0–1|外表面短波吸收率|

R3/R4 中室内长波采用均温辐射节点与固定 `h_r=5.13 W/(m²·K)`，是守恒的集中参数近似，并非逐面对逐面视角因子求解。外表面长波发射率固定 0.9。玻璃间隙热阻为固定参考值，未实现温差相关窗气层换热。

### hours 中每个对象

|字段|单位|说明|
|---|---|---|
|hour_ending|1–8760 整数|非闰年、本地标准时的结束小时；不是从 0 开始的索引|
|air_c|°C|室外空气温度，作用于渗透与天空热流参考项|
|boundary_c|4 个 °C|墙、屋面、地板、窗的外侧温度边界|
|irradiance_w_m2|4 个 W/m²|各表面平均入射短波辐照度|
|sky_ir_w_m2|W/m²|向下水平天空长波辐照度|
|solar_transmitted_w|W|透过窗进入房间的短波，已应用角度透射率，不再乘 SHGC|
|solar_absorbed_inward_w|W|玻璃吸收并向内释放的净太阳得热；本版近似直接进入空气节点|
|internal_w|W|总显热内热源|
|heat_setpoint_c、cool_setpoint_c|°C|供暖点 < 制冷点|

通常 `boundary_c` 是物理温度，程序再加外表面太阳和天空热流。若上游已经提供包含这些热流的 sol-air 温度，应令该表面 `irradiance_w_m2=0`、`sky_view=0`，避免重复计算。CETUS 可将所有边界温度设为室外空气温度。

## 核心方程与截断

外侧等效温度：
`Te = boundary + (alpha*I - 0.9*Fsky*(sigma*(Tair+273.15)^4-Lsky))/ho`。

R2：`Qnet = Σ(U*A*(Te-Tzone)) + Qinf + Qinternal + 0.965*Qtransmitted + Qglass`。

R3/R4：令 `g=1/(Rsolid+1/ho)`，联立四个表面方程：
`g*(Te-Ts)+hr*(Tr-Ts)+Qradiant/A = hi*(Ts-Tzone)`，
并用 `Σ A*hr*(Tr-Ts)=0` 确定辐射节点温度 Tr。
透射短波分布：地板 f，墙 `(0.965-f)*0.155/0.323`，屋面 `(0.965-f)*0.168/0.323`，窗 0；另有 3.5% 逸出。内热源辐射部分按面积分配。

空气节点：`Qnet=Σ A*hi*(Ts-Tzone)+rho*cp*V*ACH/3600*(Tout-Tzone)+Qinternal_conv+Qglass`。

先对各项带符号求和，最后一次截断：`Qcool=max(Qnet(Tcool),0)`，`Qheat=max(-Qnet(Theat),0)`。无供暖/制冷时用代数方程求死区内自由漂移温度。所有小时没有状态传递。

## 输出

`protocol`、`resolved_input`（原始输入完整回显）、`effective_hours`（Go 实际生成的重分配驱动）、`daily_redistribution`（每日窗透射及四表面吸收能量前后审计）、`hours`、`totals`。应保留原始与有效驱动，避免把有效驱动误当气象观测。
每小时输出：`hour_ending`、`heating_kwh`、`cooling_kwh`、`signed_kwh=heating-cooling`、`electricity_kwh`、`balance`。一小时步长下 kW 平均功率与 kWh 数值相同；不能把本接口直接用于任意步长。

`balance`：
- `zone_air_c`、`surface_c`：实际空气与表面温度；R2 的表面温度仅为等效诊断值。
- `surface_to_air_w`：四项表面对空气的带符号热流。
- `boundary_to_surface_w`：四项外边界进入表面的热流。
- `contact_transfer_w`（Urban 对接扩展）：四项纯温差交换，进入建筑为正。R3/R4/R5 为 `A*g*(boundary_c-surface_c)`；R2 为 `A*U*(boundary_c-zone_air_c)`。不含 sol-air 太阳/天空辐射修正；显式 u=0 时为零。ho 沿用内核的综合膜近似。
- `infiltration_w`、`direct_internal_w`、`direct_solar_w`：进入空气节点的其他项。
- `retained_transmitted_solar_w`、`radiant_internal_w`：源项审计；R2 的前者已经计入 direct_solar，R3/R4 中已通过表面网络传给空气，**不能重复相加**。
- `net_air_w`：以上空气节点热流之和；制冷为正、供暖为负。
- `surface_balance_residual_w`：表面总输入减输出；R3/R4 应接近机器精度。

`totals` 从逐小时热负荷/电耗直接相加。所有热负荷是整个热区数值，不除以面积、不混入 COP。

## Urban 净废热反馈

Urban 适配器使用 `1000*(cooling_kwh-heating_kwh+electricity_kwh) - infiltration_w - sum(contact_transfer_w)`
作为整栋该小时净废热 Wh（一小时步长），保留负值。
`surface_to_air_w` 含室内辐射转移，`boundary_to_surface_w` 含外侧辐射修正，二者都不能直接作为扣除项。
全建筑汇总后除以场地面积，写入 ClimateInfo.coolingLoad，供 UHI 的人为热项直接读取。
外部指定的旧新版执行文件若没有 `contact_transfer_w`，适配器报错要求从当前源文件重新编译。
