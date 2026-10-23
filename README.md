# CETUS V8.3

V8.3 使用原生二进制 Energy 通信、不可变时间步输入缓存及分级响应，减少耦合试算的数据转换和重复结果构造；保留 V8.2 物理方程、光学先验、精度与收敛阈值。表面交换射线按约 40,000 条合批，长波建筑发射项在根求解内复用，点级结果每个最终物理小时刷新一次。

安装：`pip install -r requirements.txt`。

```bash
python examples/run_v8_3.py scene.shp weather.epw --output outputs/my_case
```

此示例默认 7 天顺序 spinup；效率报告为与 V8.2 相同的最热日、周期预热后 24 小时，额外顺序 spinup 为 0。二者计算时长不可直接混用。

- 当前表面引擎：`ENERGY/bin/CetusSurfaceEnergy.exe`（Windows x64）或 `CetusSurfaceEnergy`（Linux x64）。其余 Energy 程序用于历史兼容。
- 权威接口：`docs/CetusSurfaceEnergy_V8.3_Interface.md`，包含旧 JSON 入口、新二进制接口和转换工具。
- 耦合入口：`ENERGY.coupled.simulate_coupled(..., engine_protocol='binary')`；`'json'` 保留兼容模式。
- 模型物理协议名仍为 cetus-surface-7，RAD 缓存协议仍为 cetus-rad-network-8，传输协议为 CES3 v1。
- Windows/Linux 引擎已附带；其他平台或源码修改后需 Go 1.22+，可通过 CETUS_GO 指定编译器。

测试：`PYTHONPATH=. python -m unittest discover -s tests -v`；进入 `ENERGY/surface_engine` 运行 `go test -race ./...`。

模型光学先验仍见 `ENERGY/optical_priors.json`。单建筑层/表面数量可变；各建筑的表面与状态数组顺序必须遵守注册布局。求解不会自动将候选状态提交为下一时间步。

压缩包内 benchmark 目录包含效率报告、原始计时、逐时对照、测试记录、复现脚本及本次用户输入。模块本体不含运行缓存、结果或 Git 历史。基线为 KLEGB/CETUS 提交 `1c748918862fb418261dc7c69aab1718c751c04a`。
