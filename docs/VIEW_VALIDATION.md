# Flask 工作台验证（2026-10-25）

## 真实输入

使用 `G:/Ph.D/2025_UHI_Air2Air/workplace/analysis` 中的完整文件，没有裁剪建筑或替换气象：

- SHP：`shp/LCZ5/57687_549_LCZ5_3A.shp`，300 栋建筑，EPSG:32649。
- EPW：`epw/CHN_HN_Sangzhi.575540_TMYx.2007-2021.epw`。
- 时段：07-17 00:00 至 23:00，24 小时。
- 参数：`sample=0.05`，`iteration=x`，负荷变化阈值 `0.1 W/m² GFA`。
- 环境：直接调用已有 Conda CETUS 环境的 Python 3.12.13，无需预先激活；在 Flask 页面实际导入并发起计算。

结果：

| 项目 | 结果 |
| --- | --- |
| 总 GFA | 516832.1187 m² |
| RAD 点数 | 10396 |
| 精确建筑范围 | 300 / 300 |
| 地面采样网格 | 5 × 5，保留 22 点 |
| 可见交换链接 | 499187 |
| N、F 正式结果 | 各 24 个小时 |
| F 外层轮次 | 3，达到负荷阈值 |
| N HVAC + 照明累计电耗 | 0.2385692 kWh/m² GFA |
| F HVAC + 照明累计电耗 | 0.2541510 kWh/m² GFA |

| F 轮次 | 最大逐时冷负荷变化 (W/m² GFA) | 城市最大热平衡残差 (W/m² 场地) |
| --- | --- | --- |
| 1 | 3.1650415 | 7.3161e-7 |
| 2 | 0.1213884 | 4.8779e-6 |
| 3 | 0.0359899 | 2.4995e-6 |

另以 `shp/LCZ4/54848_2766_LCZ4_2A.shp` 验证 EPSG:32650 导入，431 栋建筑全部成功载入。形态分类按当前算法识别为 LCZ2，页面保留实际识别值，不以文件名指定分类。

## Windows 环境问题与修复

1. 实际进程继承了 PostgreSQL/PostGIS 的 `PROJ_LIB`、`GDAL_DATA`。该 `proj.db` 的数据库结构版本落后于 Python 环境中 GIS 库所需版本，导致 `Could not set CRS` / `DATABASE.LAYOUT.VERSION.MINOR` 错误。`view.runtime` 在导入 GIS 库前选择当前环境的数据目录，仅更新 CETUS 进程环境。
2. 普通 CMD 下直接运行 Conda Python 时，未配置该环境的 DLL 目录，NumPy 的 `linalg.solve` 延迟加载 BLAS 时可能出现 Windows `0xc06d007f` 并终止进程。启动时为当前进程配置环境 PATH 与 DLL 搜索目录，真实 SHP 场地计算及模拟已验证通过。
3. RAD 的保存逻辑原来对只读文件句柄执行 `fsync`，Windows 返回 Bad file descriptor。改为读写句柄，保留原有持久化与 CRC 校验流程。

## 自动化与浏览器检查

29 项相关自动化测试通过，覆盖新增工作台、环境发现/创建分支、错误 PROJ 环境隔离、后台任务与停止，以及现有结果档案、RAD 缓存、二进制协议和耦合接口回归。

浏览器检查覆盖地图显示与示意阴影、随机选择、类型模板 Apply、日程节点拖动、真实 SHP/EPW 导入、后台日志及 N/F 图表。便携保存测试将保存目录复制到新位置，再通过相对路径重载。

验证输出和完整 N/F 解包结果保存在本机 `outputs/view/runs/`；它们属于运行数据，不加入版本控制。
