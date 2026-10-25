# CETUS 9.2

保留 Neighborhood 中的建筑参数、Climate 与城市结果，将完整建筑结果外置为二进制档案；独立解包应用完整导出所有建筑参数和结果。

```bash
pip install -r requirements.txt
python Energy.py --input neighborhood.json --output building_results.bin
python EnergyUnpack.py building_results.bin building_results.json
```

直接运行随包 Energy/RAD 可执行文件，不校验引擎 SHA、不自动编译。预热最多 120 轮、默认阈值 0.1 K；未收敛时 warning 后继续，预热过程静默。

- [9.2 接口与文件说明](docs/INTERFACE_9.2.md)
- [9.2 测试与 Neighborhood 大小对比](docs/VALIDATION_9.2.md)
- [9.1 多波段长波物理](docs/PHYSICS_9.1.md)
- [1.9 采光与照明物理](docs/PHYSICS_1.9.md)

Python 入口：`ENERGY.coupled.simulate_coupled`（城市耦合）和 `ENERGY.simulate_surface`（无几何单栋）。Neighborhood JSON 可编辑 `energy_options` 管理照明与长波谱设置。历史版本的文档只作物理/验证记录，当前数据接口以 9.2 文档为准。

解包应用仅用 Python 标准库；在没有模拟依赖与引擎的机器上也可运行。随包原生可执行文件支持 Linux x64 和 Windows x64。
