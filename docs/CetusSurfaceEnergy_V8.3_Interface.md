# CetusSurfaceEnergy V8.3 接口说明

V8.3 优化通信、固定输入复用和结果组织，沿用 V8.2 的热平衡方程、节点定义、光学参数和收敛判据。热工模型协议名仍为 `cetus-surface-7`；新版传输协议为 CES3 v1。版本号表示不同层次，不应混淆。

## 入口和兼容性

| 入口 | 内容 | 用途 |
|---|---|---|
| `ENERGY.surface.SurfaceEngine` | 原常驻 JSON 流接口，`configure/request/trial` 保留 | 既有外部程序、完整多时间步计算 |
| `ENERGY.surface_binary.BinarySurfaceEngine` | 原生二进制流，模型注册也不使用 JSON | 高频耦合、优化算法 |
| `python -m ENERGY.surface_codec` | 独立 JSON↔CES3 文件转换 | 不方便实现二进制的外部软件 |
| `CetusSurfaceEnergy --binary` | stdin/stdout 二进制帧流，可重定向文件 | 跨语言调用、离线批处理 |

CETUS `simulate_coupled(..., engine_protocol='binary')` 默认使用新路径；可用 `engine_protocol='json'` 对照或兼容只支持 JSON 的自定义旧引擎。后者仍包含 V8.3 的长波固定发射项复用和刷新调整，不能当作原版 V8.2 性能基线。

默认输出仍包括原有 JSON 诊断文件和续算文件，现有结果读取脚本可继续使用。高频引擎通信不产生临时二进制文件。

## 固定模型和状态布局

原 JSON 模型字段、单位和含义保持不变：

- `protocol='cetus-surface-7'`、`dt_s`、`initial_c`、`cooling_cop`、`heating_cop`、`internal_radiant_fraction`、`solar_floor_fraction`。
- `surfaces`：按输入顺序排列，保留独立的 name/kind、面积、构造 R、热容/多层构造、短波反射/透射、长波发射率、内外对流和接地参数。
- `hours`：按时间顺序的边界。注册模型时须至少提供一条有效边界；每条边界的表面数组与 surfaces 顺序一致。
- `initial_state`：热节点温度数组，摄氏度。注册模型后，每次 begin_step 显式给出本步的旧状态；未给出或空数组时按注册模型的 initial_c 初始化。

节点顺序依次为各表面外节点、各层容量节点、内节点，最后为室内空气节点、室内辐射节点。每层即使容量为零，也保留其节点；没有 layers 但有效热容大于零时，生成一个容量节点。透明面的热容及 layers 必须为空/零。

每个引擎会话当前只注册一套有序建筑集合；重复 configure 替换整个集合并使所有旧时间步失效。当前接口没有多个 model_id 的并行驻留。不要跨会话复用 step_id。模型/表面顺序变化时必须重新注册，旧状态需按新布局显式转换，不能只依据数组长度判断兼容。

## 时间步缓存：不自动推进状态

调用顺序：`configure(models) → begin_step(batch) → evaluate(...) 多次 → end_step()`。

1. configure 缓存模型、拓扑和工作矩阵，每个模型可以具有不同的表面数与层数。
2. begin_step 复制一条边界和本步旧状态，返回 step_id。只有该时间步的固定输入被缓存。
3. evaluate 以同一份旧状态求解，候选边界不会替换缓存，也不会把上次试算的结果作为下次的旧状态。
4. 取得最终接受解的 final_states 后，调用者自行决定是否将它作为下一步的 initial_state。end_step 仅释放缓存，不提交状态。

固定与动态不是物理字段的永久分类。例如，通风量可以在 begin_step 注册，也可以在每次 evaluate 时覆盖。未列入 dynamic_fields 的字段沿用本步注册值，而不是上次试算值。

独立调用方无需缓存时，继续使用原 JSON request 接口即可。支持非 3600 秒的 Energy 时间步；CETUS 的 UHI 耦合仍要求 3600 秒。

## 分级响应与显式输入参数

`evaluate(hours, response_level='totals', return_state=False, aggregation='batch', dynamic_fields=FIELDS)`。

| 参数 | 允许值 | 含义 |
|---|---|---|
| response_level | totals / coupling / full | 响应范围 |
| return_state | false / true | 是否附带每栋建筑最终热节点状态；与响应级别独立 |
| aggregation | batch / building | 是否额外返回逐建筑空气/地面反馈 |
| dynamic_fields | Hour 字段名序列 | 本次覆盖哪些字段；支持空序列 |

所有级别都返回 air_w、ground_w、max_abs_residual_w。前两个分别为全批次对空气、地面的反馈功率，单位 W；第三个为单建筑整体平衡残差绝对值的最大值。

- totals：只返回上述摘要及可选的 building_totals/final_states。
- coupling：再返回 surface_temperatures，逐建筑 name→外表面温度字典，单位 °C。
- full：再返回 batch，包含原协议的逐表面和逐建筑完整诊断。return_state=true 时，batch 内有 final_state，同时在顶层提供 final_states。
- building_totals 的每行为 `[air_w, ground_w]`，顺序与注册建筑一致。

所有级别都执行同一热平衡求解和整体残差检查。totals/coupling 跳过详细表面诊断量的构造。若研究需要逐表面 BodyResidual 等详细检查，应请求 full。

候选字段包括 air_c、ground_c、sky_ir_w_m2、internal_w、heat_setpoint_c、cool_setpoint_c、ventilation_w_k、additional_transmitted_w、additional_glass_inward_w、irradiance_w_m2、ho_w_m2k、network_ir_w_m2。

network_ir_w_m2 为空数组表示使用原天空/地面长波边界；非空时必须与本建筑表面数量一致。它表示入射长波 W/m²，不是净长波；floor 继续按接地边界处理。没有透明面时不能施加额外 glass_inward 得热。额外透射/玻璃得热为兼容验证字段，不应重复计入光学模型已经计算的得热。

## Python 原生调用

```python
from ENERGY.surface_binary import BinarySurfaceEngine

# models 是原 build_surface_input 生成的模型列表。
# hours 是每栋建筑当前这一步的一条有效 Hour 字典。
batch = [{'hours': [h], 'initial_state': old}
         for h, old in zip(hours, previous_states)]

with BinarySurfaceEngine() as engine:
    engine.configure(models)
    step_id = engine.begin_step(batch)
    totals = engine.evaluate(hours, response_level='totals')
    coupling = engine.evaluate(hours, response_level='coupling')
    accepted = engine.evaluate(hours, response_level='full', return_state=True)
    next_states = accepted['final_states']
    engine.end_step()
```

如果只改空气温度与地温，可传仅含这两个键的候选字典，并设置 `dynamic_fields=('air_c','ground_c')`。CETUS 在有 RAD 网络时覆盖这两个温度和 network_ir_w_m2；其他本小时边界不重复传输。

客户端单实例为串行请求，不支持多个线程同时调用。多研究情景可使用多个引擎实例。默认超时 120 秒；可通过构造参数 timeout 调整。CETUS_ENERGY_WORKERS 控制 Go 并行度，默认 4，当前最多 8。

## JSON / 二进制文件适配

在模块根目录下运行：

```bash
python -m ENERGY.surface_codec encode requests.json requests.ces
ENERGY/bin/CetusSurfaceEnergy --binary < requests.ces > responses.ces
python -m ENERGY.surface_codec decode responses.ces results.json --models models.json --level full --state
```

Windows 可在 cmd 中使用 `ENERGY\bin\CetusSurfaceEnergy.exe --binary < requests.ces > responses.ces`，避免通过可能按文本处理数据的 shell 管道转发二进制。

requests.json 为按顺序执行的请求数组。以下为结构示意；models、batch 和 hours 必须填入真实数据，不可原样运行：

```json
[
  {"op":"register_model", "models":[]},
  {"op":"begin_step", "step_id":1, "batch":[]},
  {"op":"evaluate", "step_id":1, "hours":[], "response_level":"full", "return_state":true},
  {"op":"end_step", "step_id":1}
]
```

解码 CLI 的 --level、--state、--aggregation 必须对应文件中 evaluate 请求的参数；同一响应文件应采用同一组输出参数，混合级别响应请在程序中按 request_id 选择对应 decode_evaluate 参数。models.json 为注册时的有序模型列表。

## CES3 v1 字节协议

所有整数无符号、小端；浮点为 IEEE 754 binary64 小端。热节点和求解精度仍为 float64。以下顺序固定，不使用结构体内存镜像。

帧头共 24 字节：

| 偏移 | 类型 | 内容 |
|---:|---|---|
| 0 | 4 bytes | ASCII CES3 |
| 4 | uint16 | 版本 1 |
| 6 | uint16 | 操作码 |
| 8 | uint64 | request_id，响应原样带回 |
| 16 | uint64 | payload 字节数，不包含头部 |

单帧最大 256 MiB。数组 = uint32 元素数 + 连续 float64；字符串 = uint32 UTF-8 字节数 + UTF-8 数据，无终止符。单帧错误返回 op=65535、同 request_id、UTF-8 错误文本；业务错误不会提交新的旧状态。畸形帧头或截断输入会关闭流。

操作码：1 注册模型，2 注册时间步，3 求解，4 释放时间步。

- op1：uint32 模型数；随后按模型顺序编码。每个模型依次为 protocol 字符串、MODEL_SCALARS 六个 float64、uint32 表面数、逐表面定义、uint32 小时数、逐小时定义、initial_state 数组。每表面为 name/kind 字符串、MODEL_SURFACE_FIELDS 固定数值字段、uint32 层数、逐层 R/C 两个 float64。响应为 uint32 注册模型数。
- op2：uint64 step_id（非零）、uint32 建筑数；每建筑依次为 initial_state 数组、完整 Hour。响应为 uint64 step_id。
- op3：uint64 step_id、uint32 level（0/1/2）、uint32 return_state（0/1）、uint32 aggregation（0/1）、uint32 动态字段掩码；随后逐建筑按 FIELDS 顺序编码被掩码选中的字段。标量为 float64，数组为上述数组格式。
- op4：uint64 step_id；响应原样返回。旧 step_id 或已释放缓存会被拒绝。

完整 Hour 为 SCALARS 的九个 float64，随后 ARRAYS 的三个数组。可选额外得热缺省为 0，对流和网络长波数组缺省为空。

op3 响应先为 uint32 建筑数、三个 float64（air_w / ground_w / max_abs_residual_w）。若 aggregation=1，接着按建筑顺序写各自 air_w/ground_w 两个 float64。然后逐建筑写响应：level=0 无诊断体；level=1 为 uint32 表面数及逐表面外温；level=2 为 OUTPUT_FIELDS 数值、uint32 表面数、逐表面的 SURFACE_FIELDS 数值。若 return_state=1，在每栋建筑诊断体后附加状态数组。

固定字段顺序的权威定义位于 ENERGY/surface_codec.py，Go 对应 ENERGY/surface_engine/binary.go。下面列出本版本的字段顺序，新增字段或改变顺序必须升级传输版本。

MODEL_SCALARS：

```text
0: dt_s
1: initial_c
2: cooling_cop
3: heating_cop
4: internal_radiant_fraction
5: solar_floor_fraction
```

MODEL_SURFACE_FIELDS：

```text
0: area_m2
1: resistance_m2k_w
2: thermal_mass_j_m2k
3: reflectance
4: transmittance
5: emissivity
6: hi_w_m2k
7: ho_w_m2k
8: sky_view
9: ground_view
10: ground_contact_w_m2k
11: absorbed_inside_fraction
```

SCALARS：

```text
0: air_c
1: ground_c
2: sky_ir_w_m2
3: internal_w
4: heat_setpoint_c
5: cool_setpoint_c
6: ventilation_w_k
7: additional_transmitted_w
8: additional_glass_inward_w
```

ARRAYS：

```text
0: irradiance_w_m2
1: ho_w_m2k
2: network_ir_w_m2
```

OUTPUT_FIELDS：

```text
0: radiant_c
1: longwave_to_network_w
2: zone_c
3: hvac_w
4: electricity_w
5: convection_to_air_w
6: ventilation_to_air_w
7: hvac_to_air_w
8: longwave_to_ground_w
9: contact_to_ground_w
10: longwave_to_sky_w
11: storage_w
12: balance_residual_w
```

SURFACE_FIELDS：

```text
0: structure_into_interior_w
1: exterior_convection_to_air_w
2: interior_convection_to_zone_w
3: interior_longwave_to_surface_w
4: transmitted_solar_deposited_w
5: exterior_contact_to_ground_w
6: external_net_lw_w
7: body_balance_residual_w
8: exterior_c
9: interior_c
10: storage_w
11: storage_delta_kwh
12: incident_w
13: reflected_w
14: absorbed_w
15: transmitted_w
```
