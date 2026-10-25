# CETUS 9.2

人工可视化操作：双击根目录 `StartView.bat`，或运行 `python -m view --open`。Flask 工作台支持建筑地图选择、参数与日程编辑、Neighborhood 便携保存及 N/F 计算对比。首次启动自动准备 Python 环境和依赖，详见 [可视化模块说明](view/README.md)。

保留 Neighborhood 中的建筑参数、Climate 与城市结果，将完整建筑结果外置为二进制档案；独立解包应用完整导出所有建筑参数和结果。

```bash
pip install -r requirements.txt
python -m ENERGY --input neighborhood.json --output building_results.bin
python -m ENERGY.unpack building_results.bin building_results.json
```

直接运行随包 Energy/RAD 可执行文件，不校验引擎 SHA、不自动编译。预热最多 120 轮、默认阈值 0.1 K；未收敛时 warning 后继续，预热过程静默。

- [9.2 接口与文件说明](docs/INTERFACE_9.2.md)
- [9.2 测试与 Neighborhood 大小对比](docs/VALIDATION_9.2.md)
- [9.1 多波段长波物理](docs/PHYSICS_9.1.md)
- [1.9 采光与照明物理](docs/PHYSICS_1.9.md)

Python 入口：`ENERGY.coupled.simulate_coupled`（城市耦合）和 `ENERGY.simulate_surface`（无几何单栋）。Neighborhood JSON 可编辑 `energy_options` 管理照明与长波谱设置。历史版本的文档只作物理/验证记录，当前数据接口以 9.2 文档为准。

解包应用仅用 Python 标准库；在没有模拟依赖与引擎的机器上也可运行。随包原生可执行文件支持 Linux x64 和 Windows x64。

目录职责：`ENERGY/` 包含能耗 Python 接口、命令行、结果存储和配置；`view/` 为本地可视化；`test/` 集中管理测试、验证脚本、示例与数据；`docs/` 保存接口、方法及发布文件清单。根目录只保留 README、运行依赖和启动 BAT。

```bash
pip install -r test/requirements.txt
python -m test -q
python -m test.run_csv
python -m test.run_native
```

原根目录的 `Energy.py`、`EnergyUnpack.py`、`result_store.py` 已迁入 `ENERGY/`，不再保留重复入口。结果读写使用 `from ENERGY.result_store import read_results, export_json`。测试路径规则与独立目录运行方式见 [测试说明](test/README.md)。
