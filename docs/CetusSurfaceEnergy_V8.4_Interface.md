# CetusSurfaceEnergy V8.4 接口增量

物理模型协议仍为 cetus-surface-7；传输为 CES3 v1。V8.3 的字节字段、操作 1–4 和返回布局保持兼容，详见 CetusSurfaceEnergy_V8.3_Interface.md。本文是 V8.4 的权威增量说明。新增操作需要本包的 V8.4 引擎。

## 不变的物理输入

每次 begin_step 注册同一物理时间步的固定边界和不可变 initial_state。候选试算和 get_result 均不提交下一步状态；下一个物理时间步必须显式提供旧状态。每个新时间步清除上一步的候选缓存和数值初值，避免模型/时间步串用。

## 原生 Python 接口

```python
from ENERGY.surface_binary import BinarySurfaceEngine

with BinarySurfaceEngine(warm_start=True, candidate_cache=True) as engine:
    engine.configure(models)
    step_id = engine.begin_step(batch)
    totals = engine.evaluate(hours, response_level='totals')
    candidate = engine.last_candidate_id
    coupling = engine.get_result(candidate, response_level='coupling', return_state=False)
    full = engine.get_result(candidate, response_level='full', return_state=True)
    profile = engine.profile()
    engine.end_step()
```

- warm_start 控制是否以上一候选的收敛节点温度作为数值迭代初值，默认 true。蓄热项始终使用本物理时间步的 initial_state，不会沿候选链推进。原有 1e-10 K 内部迭代判据和热平衡检查保持；失败时清除猜测，使用旧状态初值重试一次。
- candidate_cache 控制对相同完整 Hour 的解复用，默认 true；缓存比较包含全部 9 个标量和 3 个数组，按数值位模式比较，不使用温度近似匹配。
- evaluate 的 response_level、return_state、aggregation、dynamic_fields 与 V8.3 相同。新客户端通过操作 7 接收候选标识，存于 last_candidate_id，结果字典结构不增加标识字段。
- get_result 不接收新的候选边界；从已收敛节点温度生成指定级别的响应，不再求解节点。totals→coupling→full 升级仍执行整体热量残差检查。
- 当前只保留最近一次成功 evaluate 的候选；下一次成功 evaluate 会更换 candidate_id，即使输入数值相同。旧标识、end_step 后的标识、重新 configure 或更改 solver_options 后的标识均被拒绝。
- 一个引擎会话仍只有一套注册建筑集合，客户端仍为串行请求。多情景并行请使用多个实例。
- 原 JSON SurfaceEngine 保留原有接口和冷初值策略，适合既有独立研究。新的候选/性能接口可以通过二进制原生客户端或 JSON 文件适配器使用。

## CETUS 耦合选项

simulate_coupled 新增以下可选项，默认全部启用优化：

| 参数 | 默认值 | 含义 |
|---|---|---|
| energy_warm_start | true | 建筑内部非线性迭代初值复用 |
| candidate_cache | true | 相同边界缓存及最终结果升级复用 |
| root_memoization | true | 同一长波外迭代内，相同四温度候选的残差复用 |
| longwave_mode | affine | affine 响应预计算；direct 使用原矩阵边界函数 |

root_memoization 每轮长波外迭代清空，不会把不同建筑辐射温度下的残差混用。它只命中完全相同的四温度向量。

longwave_mode='affine' 在每轮外迭代固定建筑发射和天空输入后，建立 J=J0+Jg*Lg+Ja*La。Jg/Ja 及其组间交换响应按固定光学网络预计算，J0 随外迭代更新。不同于代理模型或拟合，它是原灰漫辐射度线性系统的代数重排；运算顺序改变可能产生浮点舍入差。

CETUS 元数据 coupled.engine_profile 给出 Go 端统计，coupled.optimizations 记录选项。hourly 新增 trial_cache_hits；evaluations 仍为根求解器调用残差函数的次数，包含缓存命中，不能当作 Go 实际求解次数。

关闭所有新选项可用于局部消融，但正式 V8.3 对照应使用未修改的 V8.3 模块，因为 V8.4 还包含计数和诊断代码组织调整。

## CES3 v1 新增操作

继承 24 字节帧头、长度检查、浮点及数组布局。操作 1–4 保持原义。

| 操作码 | 请求载荷 | 响应载荷 |
|---:|---|---|
| 5 | uint64 step_id | uint64 当前 candidate_id |
| 6 | uint64 step_id、uint64 candidate_id、uint32 level、uint32 return_state、uint32 aggregation | 与旧操作 3 完全相同的分级响应体 |
| 7 | 与旧操作 3 完全相同 | uint64 candidate_id，随后为旧操作 3 响应体 |
| 8 | uint32 warm_start、uint32 candidate_cache，各为 0/1 | 原样返回两个标志；并使旧候选失效 |
| 9 | 空 | 8 个 uint64 计数、4 个 float64 秒数 |

操作 9 的字段依次为：evaluate_requests、get_result_requests、building_solves、building_cache_hits、diagnostic_rebuilds、nonlinear_iterations、warm_starts、cold_fallbacks、decode_validate_seconds、parallel_solve_seconds、result_format_seconds、frame_write_seconds。

building_solves 是实际建筑节点求解次数，冷初值回退另计一次。building_cache_hits 是逐建筑的边界完全匹配命中数；get_result 不计入这个命中数。nonlinear_iterations 是全部建筑内部矩阵求解迭代次数之和，不是物理时间步数。

时间为 Go 端墙钟累计：decode 包含候选字段解析和校验；parallel_solve 包含并行建筑求解、缓存查询及响应级别提升；result_format 包含批次汇总和二进制结果构造，get_result 的诊断重建也计入该项；frame_write 包含输出流写入和等待对端消费。线程 CPU 时间之和不能与这些墙钟值混用。注册模型会重置计数；profile 当前这次响应的写入尚未包括在返回值中。

## JSON 文件适配器增量

python -m ENERGY.surface_codec 支持新增 op 名称：candidate_id、get_result、solver_options、profile。原 evaluate 仍使用操作 3，不改变旧文件格式。示例：

```json
{"op":"get_result", "step_id":1, "candidate_id":1,
 "response_level":"full", "return_state":true, "aggregation":"batch"}
```

```json
{"op":"solver_options", "warm_start":true, "candidate_cache":true}
```

解码 get_result 时，--level/--state/--aggregation 仍须与请求一致。脚本可解码候选 ID 和 profile。外部交互式研究优先使用原生客户端，以便显式管理返回的候选标识。

## 数值与兼容边界

优化保留 float64、物理方程、预热阈值和整体热平衡检查。改变迭代初值与代数运算顺序可能改变根求解次数及末位数字，不承诺与 V8.3 逐位相同。对照需报告温度、热流、状态和残差误差，而不能仅比较文件哈希。
