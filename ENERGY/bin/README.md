# CETUS 9.2 可执行内核

- `CetusSurfaceEnergy` / `CetusSurfaceEnergy.exe`：当前默认表面热平衡内核，支持 9.1 多波段物理和 CES3 二进制协议；9.2 数据接口沿用此内核。
- `MoosasEnergy` / `MoosasEnergy.exe`：保留的旧稳态接口。
- `MoosasEnergy-legacy` / `MoosasEnergy-legacy.exe`：更早的兼容接口。

Linux x64 / Windows x64 可直接运行。启动不校验可执行文件 SHA，不检查源码时间，不自动编译。缺失可执行文件时明确报错。源文件仅供审阅。

当前应用入口为根目录 `Energy.py` 和 `EnergyUnpack.py`。完整接口见 `docs/INTERFACE_9.2.md`。
