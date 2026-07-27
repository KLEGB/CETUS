# MoosasUrban UHI 参数校准模块技术文档

本文档面向 Coding Agent，提供基于 Optuna 多变量 TPE 贝叶斯优化的 MoosasUrban UHI 参数校准模块的完整实现规范。目标是在黑箱正向模拟条件下，仅优化 `settingInfo` 中的 19 个物理参数，使 35,880 个观测点的整体损失（RMSE 或 MAE）最小化。

---

## 1. 架构与选型概述

### 1.1 问题结构

MoosasUrban UHI 的校准问题具有以下关键特征，直接决定了算法选型：

| 维度 | 数值 | 含义 |
|---|---|---|
| 待校准参数 θ | 19 个连续浮点数 | `settingInfo` 中的物理参数 |
| 搜索域 | 默认值 ±20% | 每个参数独立的有界区间 |
| 自变量 X | 35,880 组 | `climateInfo` + `siteInfo`，固定不变 |
| 因变量 Y | 35,880 个真值 | 实测 `Tair`、`Tground` 等 |
| 单次模拟耗时 | ≈ 59 秒 | 产生 35,880 个观测点 |
| 总预算 | 150–300 次试验 | 约 2.5–5 小时 |

正向模型可以形式化为 $Y_{sim} = f(X, \theta)$，校准目标为寻找 $\theta^* = \arg\min_\theta \text{RMSE}(f(X, \theta),\ Y_{true})$。

### 1.2 关键设计决策：自变量 X 不输入优化器

这是本模块最重要的架构决策。35,880 组自变量 X（气象和站点信息）**仅用于正向模拟**，不作为优化器或代理模型的输入特征。原因如下：

若将 X 和 θ 联合输入机器学习代理模型（如 MLP 或 GP Surrogate），需要先在 θ 空间进行密集采样（LHS 数百次），生成足够的训练数据。这些数据生成的模拟代价本身就已超过直接贝叶斯优化的总代价，且引入了额外的工程复杂度和数据对齐风险。

相反，35,880 个观测点的大数量使得每次试验的损失评估具有**极高的信噪比**——每次 RMSE 估计的方差极小，这正是贝叶斯优化代理模型（内部的 TPE 密度估计器）快速收敛的关键前提。X 的作用是提供稳定、可靠的损失信号，而非作为优化器的输入。

### 1.3 算法选型对比

| 算法 | 200 次试验预算 | 主要限制 |
|---|---|---|
| **多变量 TPE（本方案）** | **最优**，150–300 次可收敛 | 无显著缺点 |
| 高斯过程 BO（GP-BO） | 中等，>100 次后 O(n³) 变慢 | 19 维下协方差矩阵求逆代价高 |
| CMA-ES | 高，连续空间收敛快 | 初期需较多随机探索 |
| NSGA-II / 遗传算法 | 极低，需 5,000+ 次模拟 | 按 59s/次计算需数天 |
| ML 代理（MLP/GP+X） | 极低，数据生成代价超过直接寻优 | 本末倒置 |

**最终选型：Optuna 多变量 TPE**，配置 `multivariate=True` 和 `group=True`，捕捉 19 个参数间的联合分布与相关性。

### 1.4 19 个校准参数的物理分组

`settingInfo` 中的 19 个待校准参数按物理机制分为五组：

| 分组 | 参数名 | 物理含义 | 单位 |
|---|---|---|---|
| **辐射吸收率** | `AbsoRoad` | 道路短波吸收率 | — |
| | `AbsoGround` | 裸地短波吸收率 | — |
| | `AbsoLandscape` | 植被短波吸收率 | — |
| | `AbsoWater` | 水面短波吸收率 | — |
| | `BruntEmissivity` | Brunt 大气长波发射率截距 | — |
| **热物性** | `UDeepLand` | 植被深层导热系数 | W/(m²·K) |
| | `UDeepGround` | 硬地深层导热系数 | W/(m²·K) |
| | `UDeepWater` | 水体深层导热系数 | W/(m²·K) |
| | `CLand` | 植被层热容 | J/(m²·K) |
| | `CGround` | 硬地层热容 | J/(m²·K) |
| | `CWater` | 水体层热容 | J/(m²·K) |
| **对流/风廓线** | `c1` | 自然对流系数 | W/(m²·K^(4/3)) |
| | `a` | 气动粗糙长度 | m |
| | `HAir` | 城市冠层参考高度 | m |
| **地热边界** | `GroundTemperature` | 深层地温边界 | °C |
| | `k` | 通风阻塞修正系数 | — |
| **人为热** | `intensity` | 交通热密度因子 | veh/ha |
| | `FE` | 车队平均油耗 | L/100km |
| | `NHC` | 燃料净热值 | kJ/kg |

---

## 2. 模块设计细节

### 2.1 参数注册表（`PARAM_REGISTRY`）

参数注册表是一个字典，以参数名为键，记录每个参数的默认值、类型、物理分组和描述。搜索域由 `CalibrationConfig.bounds_ratio`（默认 0.20，即 ±20%）自动计算，也可通过 `CalibrationConfig.param_bounds` 手动覆盖。

注册表的数据结构如下：

```python
PARAM_REGISTRY: Dict[str, Dict[str, Any]] = {
    "AbsoRoad": {
        "default": 0.74,
        "calibrate": True,
        "type": "float",       # "float" | "int" | "fixed"
        "group": "radiation",
        "unit": "-",
        "desc": "道路短波吸收率"
    },
    # ... 其余 18 个参数
}

# 默认参与校准的 19 个参数名
DEFAULT_ACTIVE_PARAMS: List[str] = [
    name for name, meta in PARAM_REGISTRY.items()
    if meta["calibrate"] and meta["type"] == "float"
]
```

**不参与校准的参数**（`"calibrate": False`）包括：求解器控制参数（`outerMaxIter`、`outerTolerance`、`innerTolerance`、`relaxationFactor`）、固定模式参数（`dt`、`conductionMode`、`qlx`）以及场景固定参数（`annualDistance`、`fuelDensity`、`ReferenceHeight`、`zRef`、`coolingSetPoint`、`outSetPoint`、`inSetPoint`）。

### 2.2 参数搜索域构建（`build_param_space`）

`build_param_space` 函数从注册表和配置中构建有序的搜索域字典 `{name: (low, high)}`：

```python
def build_param_space(config: CalibrationConfig) -> Dict[str, Tuple[float, float]]:
    active = config.active_params or DEFAULT_ACTIVE_PARAMS
    ratio  = config.bounds_ratio  # 默认 0.20
    space  = {}
    for name in active:
        meta = PARAM_REGISTRY[name]
        if name in config.param_bounds:
            lo, hi = config.param_bounds[name]  # 手动覆盖优先
        else:
            default = meta["default"]
            lo = default * (1.0 - ratio)
            hi = default * (1.0 + ratio)
        space[name] = (float(lo), float(hi))
    return space
```

对于吸收率等物理上有界的参数（如 `AbsoWater` 上限为 1.0），应在 `config.param_bounds` 中手动收紧上界：

```python
config.param_bounds = {
    "AbsoWater": (0.80, 1.00),   # 物理上限 1.0
    "AbsoRoad":  (0.60, 0.90),
}
```

### 2.3 损失函数配置（`LossConfig`）

`LossConfig` 是一个 dataclass，控制损失函数的计算方式：

```python
@dataclass
class LossConfig:
    mode: Literal["rmse", "mae", "log_likelihood", "custom"] = "rmse"
    variable_weights: Dict[str, float] = field(default_factory=dict)
    # 例如 {"Tair": 2.0, "Tground": 1.0} 表示 Tair 权重是 Tground 的两倍
    obs_weights: Optional[np.ndarray] = None
    # 各观测点的空间/时间权重，shape=(N,)；None 表示均匀权重
    sigma_obs: float = 1.0
    # "log_likelihood" 模式下的观测噪声标准差（K）
    custom_fn: Optional[Callable] = None
    # "custom" 模式下的损失函数：fn(simulated, ground_truth) -> float
```

统一损失计算函数 `compute_loss` 的签名为：

```python
def compute_loss(
    simulated: np.ndarray,    # shape=(N, M)，N=观测点数，M=变量数
    ground_truth: np.ndarray, # shape=(N, M)
    loss_cfg: LossConfig,
    variable_names: Optional[List[str]] = None,
) -> float:
    ...
```

加权 RMSE 的计算公式为：

$$\text{RMSE} = \sqrt{\frac{\sum_i w_i (y_{sim,i} - y_{true,i})^2}{\sum_i w_i}}$$

### 2.4 全局校准配置（`CalibrationConfig`）

`CalibrationConfig` 是校准运行的完整配置容器，分为四个逻辑组：

```python
@dataclass
class CalibrationConfig:
    # ── 模拟器集成 ──────────────────────────────────────────────────────────
    simulator_script: str = "UHI/csv_calculator.py"
    # 模拟器脚本路径（相对于项目根目录）
    simulator_positional_args: List[str] = field(default_factory=list)
    # 固定位置参数，例如 ["calibration/input_100.csv"]
    # 注意：不含 settingInfo 覆盖项和输出路径，这两项由目标函数动态拼接
    output_parser: Optional[Callable] = None
    # 输出解析器，签名：parser(output_path: str) -> np.ndarray, shape=(N, M)
    ground_truth: Optional[np.ndarray] = None
    # 真值数组，shape=(N, M)，与 output_parser 返回值完全对齐
    loss_variables: List[str] = field(default_factory=lambda: ["Tair", "Tground"])
    loss_config: LossConfig = field(default_factory=LossConfig)
    output_dir: str = "calibration/tmp"
    # 临时输出目录；每次试验写入独立子目录以支持并行

    # ── 参数搜索空间 ─────────────────────────────────────────────────────────
    active_params: Optional[List[str]] = None
    # None 表示使用全部 19 个默认校准参数
    param_bounds: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    # 手动覆盖搜索域，格式：{name: (low, high)}
    bounds_ratio: float = 0.20  # ±20%

    # ── 优化超参数 ───────────────────────────────────────────────────────────
    n_trials: int = 200          # 总试验次数（建议 150-300）
    n_startup_trials: int = 40   # 随机初始化阶段（建议 30-50）
    n_jobs: int = 1              # 并行进程（>1 时必须设置 storage）
    sampler: str = "tpe"         # "tpe" 或 "cmaes"
    timeout_per_trial: Optional[float] = 180.0  # 单次超时（秒）
    preheat: int = 3             # 模拟预热循环次数

    # ── 持久化 ───────────────────────────────────────────────────────────────
    study_name: str = "uhi_calibration"
    storage: Optional[str] = None
    # SQLite URL，例如 "sqlite:///calibration/cal.db"
    # None 表示内存存储（不支持断点续跑和并行）
    python_executable: str = field(default_factory=lambda: sys.executable)
```

### 2.5 目标函数设计（`make_objective`）

目标函数是校准模块的核心，封装了从参数采样到损失返回的完整流程。以下是每个步骤的设计要点：

**步骤 1：采样参数。** Optuna 的 TPE 采样器从参数空间中建议一组参数值 `{name: float}`。

**步骤 2：生成唯一输出路径。** 为避免并行试验之间的文件冲突，每次试验必须生成唯一的临时输出文件名：

```python
tmp_output = str(
    output_dir / f"trial_{trial.number:04d}_{uuid.uuid4().hex[:6]}.csv"
)
```

**步骤 3：拼接 CLI 命令。** 将采样的参数以 `--ParamName value` 形式追加到模拟器命令：

```python
cmd = [
    config.python_executable,
    config.simulator_script,
    *config.simulator_positional_args,  # 例如 ["calibration/input_100.csv"]
    tmp_output,                          # 输出路径
    "--preheat", str(config.preheat),
]
for name, val in params.items():
    cmd += [f"--{name}", str(val)]
# 最终命令示例：
# python UHI/csv_calculator.py calibration/input_100.csv
#   calibration/tmp/trial_0042_a3f8c1.csv
#   --preheat 3
#   --AbsoRoad 0.7512 --AbsoGround 0.7389 --BruntEmissivity 0.618 ...
```

**步骤 4：通过 subprocess 运行模拟器。** 使用 `subprocess.run` 并设置超时，捕获非零退出码：

```python
proc = subprocess.run(
    cmd,
    capture_output=True,
    text=True,
    timeout=config.timeout_per_trial,  # 默认 180 秒
)
if proc.returncode != 0:
    return float("inf")  # 标记为失败，不终止优化
```

**步骤 5：解析输出并计算损失。** 调用 `output_parser` 将输出文件转换为 numpy 数组，再调用 `compute_loss`：

```python
simulated = config.output_parser(tmp_output)  # shape=(N, M)
loss = compute_loss(simulated, ground_truth, loss_cfg, variable_names)
return loss
```

**容错设计：** 超时、非零退出码、输出解析异常均返回 `float("inf")`，不抛出异常，确保优化循环不中断。临时文件在解析完成后立即删除。

### 2.6 主校准流程（`run_calibration`）

```python
def run_calibration(config: CalibrationConfig, callbacks=None) -> optuna.Study:
    param_space = build_param_space(config)

    # 创建 TPE 采样器（多变量模式）
    sampler = TPESampler(
        n_startup_trials=config.n_startup_trials,
        multivariate=True,        # 关键：建模 19 个参数间的联合分布
        group=True,               # 按物理分组采样，提升高维效率
        seed=42,
        consider_magic_clip=True,
    )

    # 创建或加载 study（支持断点续跑）
    study = optuna.create_study(
        study_name=config.study_name,
        direction="minimize",
        sampler=sampler,
        storage=config.storage,
        load_if_exists=True,      # 断点续跑的关键
    )

    # Warm start：将默认参数值作为第一个试验点入队
    study.enqueue_trial({name: PARAM_REGISTRY[name]["default"]
                         for name in param_space})

    objective = make_objective(config, param_space)
    study.optimize(
        objective,
        n_trials=config.n_trials,
        n_jobs=config.n_jobs,
        callbacks=callbacks or [],
    )
    return study
```

### 2.7 结果导出

校准完成后，调用以下函数导出结果：

`export_best_params(study, output_json, output_cli)` 导出两个文件：

- `best_params.json`：包含最优损失值、试验编号、参数值及与默认值的偏差百分比。
- `best_params_cli.txt`：可直接拼接到模拟器命令后的 CLI 字符串，例如 `--AbsoRoad 0.7512 --CGround 9823.4 ...`。

`export_trials_csv(study, output_csv)` 将所有试验历史导出为 CSV，用于收敛分析。

`analyze_importance(study, output_png)` 使用 fANOVA 方法计算参数重要性，输出水平条形图。建议在 >100 次有效试验后调用，结果更可靠。

### 2.8 早停回调

```python
def make_early_stopping_callback(patience=40, min_improvement=1e-4):
    state = {"best": float("inf"), "count": 0}
    def callback(study, trial):
        if state["best"] - study.best_value > min_improvement:
            state["best"]  = study.best_value
            state["count"] = 0
        else:
            state["count"] += 1
        if state["count"] >= patience:
            study.stop()
    return callback
```

---

## 3. 输入输出协议与集成规范

### 3.1 模拟器 CLI 接口

两个计算器（`csv_calculator.py` 和 `raster_calculator.py`）均支持通过 CLI 覆盖任意 `settingInfo` 字段，格式为 `--ParamName value`。完整支持的字段列表见 `input_protocol.md`。

特殊解析规则：

- `--dt none`：切换到稳态模式（不使用时步积分）
- `--dt default`：恢复内置默认时步（3600 秒）
- `--qlx`：接受逗号分隔的 24 个浮点数（小时形状因子序列）
- `--conductionMode`：接受字符串，例如 `storage_plus_deep`

校准模块**不应**覆盖 `dt`、`conductionMode`、`qlx` 这三个固定参数，它们在 `PARAM_REGISTRY` 中标记为 `"type": "fixed"`，`build_param_space` 会自动跳过。

### 3.2 CSV 输出解析器（`make_csv_output_parser`）

适用于 `csv_calculator.py`，从输出 CSV 中提取指定列，返回 `shape=(N_rows, M)` 的 numpy 数组，其中 `N_rows = n_cases × 24`。

```python
def make_csv_output_parser(variables=None):
    import pandas as pd
    if variables is None:
        variables = ["Tair", "Tground"]
    cols_lower = [v.lower() for v in variables]

    def parser(output_path: str) -> np.ndarray:
        df = pd.read_csv(output_path)
        df.columns = [c.lower() for c in df.columns]
        return df[cols_lower].values.astype(np.float64)

    return parser
```

**真值对齐要求：** 真值 CSV 必须与模拟输出 CSV 具有完全相同的行顺序和行数（均为 `n_cases × 24`），按 `caseid` 和 `t` 的顺序排列。不建议在损失计算时进行 join 对齐，直接按行对应效率更高且不易出错。

### 3.3 Raster 输出解析器（`make_raster_output_parser`）

适用于 `raster_calculator.py`，从 24 个小时 GeoTIFF 文件中提取指定波段，返回 `shape=(pixels × 24, M)` 的 numpy 数组。

波段顺序（固定）：1=QS, 2=QL, 3=QV, 4=QA, **5=Tair**, **6=Tground**, 7=Tambient。

```python
def make_raster_output_parser(variables=None, n_hours=24):
    BAND_MAP = {"QS": 1, "QL": 2, "QV": 3, "QA": 4,
                "Tair": 5, "Tground": 6, "Tambient": 7}
    if variables is None:
        variables = ["Tair", "Tground"]
    band_indices = [BAND_MAP[v] for v in variables]

    def parser(output_base: str) -> np.ndarray:
        import rasterio
        base = Path(output_base)
        # 若 output_base 以 .tif 结尾：output_00.tif ... output_23.tif
        # 否则视为目录：hour_00.tif ... hour_23.tif
        if base.suffix.lower() == ".tif":
            get_path = lambda h: base.parent / f"{base.stem}_{h:02d}.tif"
        else:
            get_path = lambda h: base / f"hour_{h:02d}.tif"

        chunks = []
        for h in range(n_hours):
            with rasterio.open(get_path(h)) as src:
                bands = np.stack([src.read(b).ravel() for b in band_indices], axis=-1)
            chunks.append(bands)
        return np.concatenate(chunks, axis=0)  # shape=(pixels*24, M)

    return parser
```

**真值对齐要求：** 真值数组必须具有相同的像素顺序（行优先展平）和相同的 24 小时顺序。建议真值也通过同一解析器从参考模拟输出中提取，以确保完全对齐。

### 3.4 并行运行规范

当 `n_jobs > 1` 时，必须同时设置 `storage`（SQLite 或 PostgreSQL URL）。并行运行时的文件隔离依赖于每次试验的唯一临时文件名（`trial_{number}_{uuid6}`），不得使用固定文件名。

```bash
# 启动两个并行校准进程（共享同一 SQLite study）
python calibration/calibrate.py --input input.csv --ground-truth gt.csv \
    --n-trials 200 --n-jobs 1 \
    --storage sqlite:///calibration/cal.db --study-name uhi_run1 &

python calibration/calibrate.py --input input.csv --ground-truth gt.csv \
    --n-trials 200 --n-jobs 1 \
    --storage sqlite:///calibration/cal.db --study-name uhi_run1 &
```

---

## 4. 完整代码示例

### 4.1 CSV 模式校准（最小可运行示例）

```python
# run_csv_calibration.py
import numpy as np
import pandas as pd
from calibration.calibrate import (
    CalibrationConfig, LossConfig,
    make_csv_output_parser, make_early_stopping_callback,
    export_best_params, export_trials_csv, analyze_importance,
    run_calibration,
)

# 1. 加载真值（因变量 Y_true）
# 真值 CSV 格式与模拟器输出完全相同，包含 caseid, t, Tair, Tground 等列
gt_df = pd.read_csv("calibration/ground_truth.csv")
gt_df.columns = [c.lower() for c in gt_df.columns]
gt_array = gt_df[["tair", "tground"]].values.astype(np.float64)
# gt_array.shape == (35880, 2)，对应 1495 个 case × 24 小时 × 2 个变量

# 2. 组装配置
cfg = CalibrationConfig(
    # 模拟器（X 已封装在输入文件中）
    simulator_script="UHI/csv_calculator.py",
    simulator_positional_args=["calibration/input_100.csv"],
    output_dir="calibration/tmp",
    preheat=3,

    # 输出解析与损失
    output_parser=make_csv_output_parser(["Tair", "Tground"]),
    ground_truth=gt_array,
    loss_variables=["Tair", "Tground"],
    loss_config=LossConfig(
        mode="rmse",
        variable_weights={"Tair": 1.0, "Tground": 1.0},
    ),

    # 参数空间（默认 19 个参数，±20%）
    bounds_ratio=0.20,
    # 可选：手动收紧吸收率上界
    param_bounds={"AbsoWater": (0.80, 1.00)},

    # 优化超参数
    n_trials=200,
    n_startup_trials=40,
    sampler="tpe",
    timeout_per_trial=180.0,

    # 持久化（支持断点续跑）
    study_name="uhi_csv_run1",
    storage="sqlite:///calibration/calibration.db",
)

# 3. 运行校准
study = run_calibration(
    cfg,
    callbacks=[make_early_stopping_callback(patience=40)],
)

# 4. 导出结果
export_best_params(study, "calibration/best_params.json",
                           "calibration/best_params_cli.txt")
export_trials_csv(study, "calibration/trials.csv")
analyze_importance(study, "calibration/param_importance.png")

print(f"最优 RMSE = {study.best_value:.4f} K")
print(f"最优参数 → calibration/best_params.json")
```

### 4.2 CLI 直接调用

```bash
# 基础用法
python calibration/calibrate.py \
    --simulator UHI/csv_calculator.py \
    --input calibration/input_100.csv \
    --ground-truth calibration/ground_truth.csv \
    --n-trials 200 \
    --storage sqlite:///calibration/calibration.db

# 完整参数
python calibration/calibrate.py \
    --simulator UHI/csv_calculator.py \
    --input calibration/input_100.csv \
    --ground-truth calibration/ground_truth.csv \
    --loss-mode rmse \
    --loss-vars Tair Tground \
    --n-trials 200 \
    --n-startup 40 \
    --sampler tpe \
    --patience 40 \
    --bounds-ratio 0.20 \
    --preheat 3 \
    --timeout 180 \
    --storage sqlite:///calibration/calibration.db \
    --study-name uhi_csv_run1 \
    --export-json calibration/best_params.json \
    --export-cli calibration/best_params_cli.txt \
    --export-trials calibration/trials.csv \
    --importance-png calibration/param_importance.png

# 断点续跑（重复执行同一命令即可，load_if_exists=True）
python calibration/calibrate.py \
    --simulator UHI/csv_calculator.py \
    --input calibration/input_100.csv \
    --ground-truth calibration/ground_truth.csv \
    --n-trials 300 \
    --storage sqlite:///calibration/calibration.db \
    --study-name uhi_csv_run1

# 使用最优参数运行模拟
python UHI/csv_calculator.py calibration/input_100.csv output_calibrated.csv \
    $(cat calibration/best_params_cli.txt)
```

### 4.3 Raster 模式校准

```python
# run_raster_calibration.py
import numpy as np
from calibration.calibrate import (
    CalibrationConfig, LossConfig,
    make_raster_output_parser, make_early_stopping_callback,
    export_best_params, run_calibration,
)

RASTER_DIR = "workplace/raster_UTM49/_uhiCalculate_100"

# 1. 加载真值（从参考模拟输出提取，确保像素顺序一致）
gt_parser = make_raster_output_parser(["Tair", "Tground"])
gt_array = gt_parser(f"{RASTER_DIR}/reference_output")
# gt_array.shape == (pixels * 24, 2)

# 2. 组装配置
cfg = CalibrationConfig(
    simulator_script="UHI/raster_calculator.py",
    simulator_positional_args=[
        "calibration/input_100.csv",
        f"{RASTER_DIR}/plotRatio.tif",
        "calibration/tmp_raster/output",  # 输出基路径
        "--plotRatio",    f"{RASTER_DIR}/plotRatio.tif",
        "--AreaBuilding", f"{RASTER_DIR}/building.tif",
        "--AreaRoad",     f"{RASTER_DIR}/road.tif",
        "--AreaGround",   f"{RASTER_DIR}/ground.tif",
        "--AreaLandscape",f"{RASTER_DIR}/lanscape.tif",
        "--AreaWater",    f"{RASTER_DIR}/water.tif",
        "--AreaFacade",   f"{RASTER_DIR}/facadeArea.tif",
        "--SVF",          f"{RASTER_DIR}/SVF.tif",
    ],
    output_dir="calibration/tmp_raster",
    output_parser=make_raster_output_parser(["Tair", "Tground"]),
    ground_truth=gt_array,
    loss_variables=["Tair", "Tground"],
    loss_config=LossConfig(mode="rmse"),
    bounds_ratio=0.20,
    n_trials=200,
    n_startup_trials=40,
    timeout_per_trial=300.0,
    study_name="uhi_raster_run1",
    storage="sqlite:///calibration/calibration_raster.db",
)

study = run_calibration(cfg, callbacks=[make_early_stopping_callback(40)])
export_best_params(study, "calibration/best_params_raster.json",
                           "calibration/best_params_raster_cli.txt")
```

---

## 5. 参数注册表完整清单

下表列出所有 `settingInfo` 参数的默认值、是否参与校准及默认搜索域（±20%）：

| 参数名 | 默认值 | 参与校准 | 搜索域下界 | 搜索域上界 | 物理分组 |
|---|---|---|---|---|---|
| `AbsoRoad` | 0.74 | 是 | 0.592 | 0.888 | 辐射 |
| `AbsoGround` | 0.74 | 是 | 0.592 | 0.888 | 辐射 |
| `AbsoLandscape` | 0.80 | 是 | 0.640 | 0.960 | 辐射 |
| `AbsoWater` | 0.96 | 是 | 0.768 | **1.000** | 辐射 |
| `BruntEmissivity` | 0.605 | 是 | 0.484 | 0.726 | 辐射 |
| `UDeepLand` | 0.40 | 是 | 0.320 | 0.480 | 热物性 |
| `UDeepGround` | 0.70 | 是 | 0.560 | 0.840 | 热物性 |
| `UDeepWater` | 1.00 | 是 | 0.800 | 1.200 | 热物性 |
| `CLand` | 5.0×10⁵ | 是 | 4.0×10⁵ | 6.0×10⁵ | 热物性 |
| `CGround` | 1.0×10⁴ | 是 | 8.0×10³ | 1.2×10⁴ | 热物性 |
| `CWater` | 2.1×10⁶ | 是 | 1.68×10⁶ | 2.52×10⁶ | 热物性 |
| `c1` | 1.31 | 是 | 1.048 | 1.572 | 对流 |
| `a` | 0.22 | 是 | 0.176 | 0.264 | 对流 |
| `HAir` | 10.0 | 是 | 8.000 | 12.000 | 对流 |
| `GroundTemperature` | 25.0 | 是 | 20.000 | 30.000 | 地热边界 |
| `k` | 0.10 | 是 | 0.080 | 0.120 | 地热边界 |
| `intensity` | 20.0 | 是 | 16.000 | 24.000 | 人为热 |
| `FE` | 10.9 | 是 | 8.720 | 13.080 | 人为热 |
| `NHC` | 4.5×10⁴ | 是 | 3.6×10⁴ | 5.4×10⁴ | 人为热 |
| `annualDistance` | 2.5×10⁴ | **否** | — | — | 人为热（固定） |
| `fuelDensity` | 0.0072 | **否** | — | — | 人为热（固定） |
| `ReferenceHeight` | 100.0 | **否** | — | — | 风廓线（固定） |
| `zRef` | 10.0 | **否** | — | — | 风廓线（固定） |
| `coolingSetPoint` | 26.0 | **否** | — | — | 建筑负荷（固定） |
| `outSetPoint` | 34.5 | **否** | — | — | 建筑负荷（固定） |
| `inSetPoint` | 29.9 | **否** | — | — | 建筑负荷（固定） |
| `outerMaxIter` | 50 | **否** | — | — | 求解器控制 |
| `outerTolerance` | 0.01 | **否** | — | — | 求解器控制 |
| `innerTolerance` | 1×10⁻⁴ | **否** | — | — | 求解器控制 |
| `relaxationFactor` | 0.6 | **否** | — | — | 求解器控制 |
| `dt` | 3600 | **否** | — | — | 固定 |
| `conductionMode` | `storage_plus_deep` | **否** | — | — | 固定 |
| `qlx` | 24 值序列 | **否** | — | — | 固定 |

> `AbsoWater` 的上界手动收紧至 1.000（物理上限），应在 `param_bounds` 中显式指定。

---

## 6. 实施清单

Coding Agent 在实现校准模块时，应按以下顺序完成各项工作：

1. 按照第 2.1 节定义 `PARAM_REGISTRY`，确保 19 个参数的名称与 `settingInfo.__slots__` 完全一致。
2. 实现 `LossConfig` dataclass 和 `compute_loss` 函数，支持 RMSE 和 MAE 两种模式。
3. 实现 `CalibrationConfig` dataclass，包含第 2.4 节中的全部字段。
4. 实现 `build_param_space`，按 ±`bounds_ratio` 自动计算搜索域，支持 `param_bounds` 手动覆盖。
5. 实现 `make_objective`，严格遵循第 2.5 节的六步流程，特别注意唯一临时文件名和容错返回 `float("inf")`。
6. 实现 `run_calibration`，使用 `TPESampler(multivariate=True, group=True)`，启用 `load_if_exists=True` 支持断点续跑，并将默认参数值作为 warm-start 入队。
7. 实现 `make_csv_output_parser` 和 `make_raster_output_parser`，确保输出数组的行顺序与真值完全对齐。
8. 实现 `export_best_params`、`export_trials_csv` 和 `analyze_importance` 三个结果导出函数。
9. 实现 `make_early_stopping_callback`，默认 `patience=40`。
10. 提供 CLI 入口（`argparse`），支持第 4.2 节中的全部命令行参数。
