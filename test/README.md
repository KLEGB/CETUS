# 测试与验证

所有 Python/Go 测试、测试用例、历史验证工具和示例集中在此目录。运行所需的 Energy/RAD 程序与物理接口仍放在各自生产模块中。

```text
test/
  test_*.py        Python 测试
  paths.py         统一的项目、数据、配置与输出路径
  run_csv.py       24 小时 CSV 验证入口
  run_native.py    Go 源码测试入口
  scripts/         验证、比较与绘图工具
  examples/        可执行示例
  data/            CSV 测试输入
  validation/      历史参考数据与校验清单
  native/          Energy / RAD 的 Go 测试源码
  reports/         历史测试运行记录
  output/          新产生的验证输出，不提交到版本库
```

从项目根目录运行：

```powershell
python -m pip install -r test/requirements.txt
python -m test -q
python -m test.run_csv
python -m test.run_native
```

也可直接使用 `python -m pytest test -q`。在其他工作目录运行时，将测试目录或脚本写为绝对路径：

```powershell
python -m pytest E:/PycharmProjects/CETUS/test -q
python E:/PycharmProjects/CETUS/test/run_csv.py
python E:/PycharmProjects/CETUS/test/examples/run_v1_9_standalone.py
```

模块自身的资源路径按源码位置解析，不依赖当前工作目录。`test.paths` 提供 `PROJECT_ROOT`、`TEST_ROOT`、`DATA`、`VALIDATION`、`OUTPUT`、`ENERGY_CONFIGS`。命令行显式传入的相对路径仍按调用者当前目录解析。参考数据留在 `validation/`，新生成的 CSV 与示例输出写入 `output/`。

Go 测试需要安装 Go 1.22 或更高版本；脚本会先校验版本，不自动下载编译器。`run_native` 将生产 Go 源码与对应测试复制到系统临时目录执行 `go test`，结束后清理临时副本，不生成、覆盖或替换生产可执行文件。普通模拟与解包不需要 Go。

历史四案例验证：

```powershell
python test/scripts/validate_surface.py
```

能耗接口现位于 `ENERGY`，运行模拟使用 `python -m ENERGY`，独立解包使用 `python -S -m ENERGY.unpack`。使用绝对路径调用 `ENERGY/cli.py` 或 `ENERGY/unpack.py` 同样支持从其他目录运行。
