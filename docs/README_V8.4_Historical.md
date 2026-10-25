# CETUS V8.4

V8.4 在 V8.3 的二进制 Energy 接口基础上，增加候选解复用、非线性迭代初值复用、长波仿射响应和重复残差复用。保留物理方程、float64、材料先验、采样、视因数网络和收敛阈值。

安装：`pip install -r requirements.txt`。

```bash
python test/examples/run_v8_4.py scene.shp weather.epw --output outputs/my_case
```

示例默认 7 天顺序 spinup。效率报告使用与 V8.3 一致的最热日、8 轮周期预热和正式 24 小时，额外顺序 spinup 为 0；两种运行时长不可混用。

当前引擎：ENERGY/bin/CetusSurfaceEnergy.exe（Windows x64）或 CetusSurfaceEnergy（Linux x64）。其余 Energy 程序保留历史兼容。

接口权威说明：docs/CetusSurfaceEnergy_V8.4_Interface.md；继承字段和旧操作见 V8.3_Interface。BinarySurfaceEngine 支持候选标识、get_result、profile 和可选 warm_start/candidate_cache。旧 JSON SurfaceEngine 保留。

CETUS 耦合新增可控项：energy_warm_start、candidate_cache、root_memoization、longwave_mode；详情见接口说明。新版本数值迭代初值及运算顺序变化可能导致末位差异，应按误差和残差验证，不能要求文件哈希一致。

测试：`PYTHONPATH=. python -m unittest discover -s tests -v`；进入 ENERGY/surface_engine 运行 `go test -race ./...`。

Linux/Windows x64 引擎已附带。Windows 为交叉编译，本次效率及运行验证均在 Linux 上完成。其他平台或源码修改后需 Go 1.22+，可通过 CETUS_GO 指定编译器。

热工物理协议仍为 cetus-surface-7，RAD 缓存协议仍为 cetus-rad-network-8，二进制协议 CES3 v1 新增操作 5–9。新客户端需要匹配的 V8.4 引擎。

压缩包 benchmark 目录含本轮效率报告、原始计时、数值差异、测试和消融记录及复现输入。基线为此前交付的 V8.3；V8.3 更早的源码基线为 KLEGB/CETUS 提交 1c748918862fb418261dc7c69aab1718c751c04a。
