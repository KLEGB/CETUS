# CETUS 9.2 接口与文件说明

## 本次范围

建筑参数仍由 Neighborhood 管理；Climate 及城市侧结果保持原有位置和含义。不把 Neighborhood 改成纯输入文件，不重新设计城市模型或辐射网络的管理职责。本次仅将建筑完整能耗结果移到外部文件，并复制本次建筑参数供同步分析。

## 两个应用

```bash
python Energy.py --input neighborhood.json --output building_results.bin
python EnergyUnpack.py building_results.bin building_results.json
```

Energy 应用包含 Neighborhood JSON 读取与校验，默认走现有 CES3 二进制求解接口。原生 Energy 也继续支持原 JSON 输入协议；已有 JSON 调试入口保留兼容。EnergyUnpack 是独立、仅依赖 Python 标准库的应用，不导入 Energy、NumPy、SciPy、几何库或求解器，不需调用可执行引擎。解包一次性导出所有建筑参数和全部结果，不提供筛选接口。

`Energy.py` 还在结果旁保存 `<结果名>_neighborhood.json`，其中继续包含城市 Climate 结果；建筑结果只有 `building_result_file` 引用。城市侧 CSV、守恒审计、状态检查点位于 `<结果名>_city/`。输入文件中的相对外部路径以输入 JSON 所在目录解析。

## Python 调用

```python
from ENERGY import Neighborhood
from ENERGY.coupled import simulate_coupled
n = Neighborhood.loadJSON('neighborhood.json')
n.buildings[0].WallUValue = 0.5
# 完整照明/多波段设置可编辑并保存在 Neighborhood.energy_options。
r = simulate_coupled(n, output_dir='outputs/run')
n.saveJSON('neighborhood_after.json')
print(n.building_result_file)
```

`simulate_coupled` 返回城市侧数组、逐时城市结果、汇总和 `building_result_file`。它不把建筑逐时结果挂回 `n.buildings`，不再默认写出 `building_hourly.json`。输入建筑对象保留历史结果字段以兼容旧类型定义，但完成计算后这些字段清空；Neighborhood 的 JSON 序列化不包含这些建筑结果字段，也不包含 `_energy_results`。

`simulate_surface` 独立单栋接口也写同一种结果档案，内部改用原生二进制响应；其 Python 返回值仍保留小时结果供既有单栋调用者使用，但不在运行阶段自动导出完整结果 JSON。

历史 `energySimulation`/`energySimulationSteady` 在写回城市反馈后，同样把建筑数据外置；其历史引擎没有 CES3 原始响应，采用明确标识的二进制树记录，并在档案上下文保留原始结构化输出。它们不冒充原生 CES3 数据。历史科学验证脚本可显式调用分析导出助手生成旧版诊断文件；这不属于默认 Energy 写出路径。

## 建筑标识与参数副本

`building_id` 在 Neighborhood 中持久保存，缺失时自动分配，重复 ID 报错。结果按 ID 与输入参数关联。运行档案包含：

- 原始完整建筑参数，包括日程文本；
- 实际使用的表面模型：构造热阻/热容、面积、太阳光学及长波谱参数等；
- 每个正式小时最后收敛候选的实际边界和内热源；
- 照明控制参数与逐区诊断、光谱配置；谱耦合时另保存回填后的实际发射率、反射率和透射余量；
- 各小时完整建筑/表面数值结果、照明电耗、热平衡残差和状态；
- 求解器原生全群组空气/地面交换汇总，以及任务级 HVAC/照明电耗汇总。

解包不读取用户当前的 Neighborhood 或材料文件，因此后续编辑不会改变旧结果显示的参数。

## 二进制档案

`.bin` 是 CER 版本 1 容器，内部采用 ZIP 无损压缩。建筑数值结果保存为 **Go 引擎最终 CES3 full/state 响应的原始二进制 payload**，不经 JSON 中转重编码。每小时全体建筑同批存储，保留 float64 精度和已有群组汇总；候选编号及传输帧头不作为物理结果保存。

容器包含版本/字段布局清单、建筑参数、模型参数、源气象/控制上下文、逐时原始响应、实际边界、照明补充量和最终审计。输入参数及 Python 侧附加信息采用显式小端类型编码（空值、布尔、int64、float64、UTF-8 字符串、列表、映射），不使用 pickle。只有小型档案目录清单采用 JSON，运行阶段没有完整建筑结果 JSON 编码。

完整结果只在正式小时收敛后追加。预热与中间候选不进入正式结果。文件先写 `.tmp`，完成后原子替换目标；解包拒绝未完成、未知版本或不匹配布局的文件，读取时由容器校验记录完整性。

解包后的根对象包含 `context`、`buildings`、`group_totals`、`run_metadata`。每栋建筑包含 `building_id`、`parameters`、`resolved_model`、`applied_inputs`、`hours`。所有建筑都导出；共享光谱/日程诊断在 `context` 中保存。瞬时/小时平均功率 W、累计电能 kWh、温度 °C；HOY 为零起算，默认每小时一步。

## 本地运行偏好

- Energy 启动不读取/比较引擎 SHA，不检查源码新旧，不自动编译。直接运行随包 Linux x64/Windows x64 可执行文件；缺失则明确报错。
- RAD 原生程序同样直接使用随包可执行文件，不因解压修改时间触发编译。
- 预热默认最少 4 轮、最多 120 轮，收敛阈值 **0.1 K**。达到上限仍未收敛时发出 `RuntimeWarning`，以当时状态继续正式计算。
- 预热过程无进度打印；正式小时进度仍由 `show_progress` 控制。预热历史及最后温差保存在审计中。
- 正式小时的非线性/耦合收敛检查保持原要求，正式求解失败仍报错。
- 输入/缓存/重启配置签名继续保留，它们用于防止错用模型与状态，不是可执行引擎 SHA 校验。

## 干净模块

当前默认热工内核为 `ENERGY/bin/CetusSurfaceEnergy`（Windows 为 `.exe`）；本次改变 Python 数据接口，不改变 9.1 已验证的原生物理内核。随包 `.go` 文件供审阅，运行不要求 Go。

`MoosasEnergy` 为保留的旧稳态内核，`MoosasEnergy-legacy` 为更早的兼容内核，不能替换默认的 CetusSurfaceEnergy。模块不包含工作输出、缓存、备份或历史源码树；本次测试报告见 `VALIDATION_9.2.md`。
