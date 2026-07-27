# 城市热岛效应模型中地气热交换的参数化方法

## 摘要

本章节提出一套用于城市热岛效应数值模拟的地表-冠层空气热交换参数化框架。该框架以网格尺度的地表净得热热流密度 $I_w$ 为驱动量，通过联立求解地表温度 $T_g$ 与冠层空气温度 $T_a$ 的非线性方程组，输出各热通量分量及温升结果。模型在传统线性风速相关系数的基础上，系统性地引入了基于 Monin-Obukhov 相似理论的强制对流参数化、基于湍流自然对流理论的浮力驱动对流参数化、基于分子导热的换热下限约束、基于 Force-Restore 方法的地层蓄热参数化，以及基于城市形态修正风速的冠层通风散热参数化。所有输入参数均为可直接观测或由城市形态计算得出的量，模型仅含一个待校准的经验系数。

---

## 1. 模块计算逻辑概述

本模型以城市网格为基本计算单元，通过逐时求解地表与冠层空气的耦合能量平衡方程组，输出0–23时的温度场及各热流密度分量。本章从模型输入、整体计算流程（含预热机制）、逐时求解逻辑，以及模型输出四个层次，完整描述模块的计算逻辑。

### 1.1 模型输入

模型的输入由两类参数构成：描述气象边界条件的**气候信息**（`ClimateInfo`）和描述场地物理属性的**场地信息**（`SiteInfo`）。两者均以24小时为一个计算周期进行组织。

**气候信息（`ClimateInfo`）** 包含以下逐时气象序列（每项均为长度为24的数组，下标对应小时 $t = 0, 1, \ldots, 23$）：

| 字段名 | 含义 | 单位 |
| :--- | :--- | :--- |
| `Tb` | 背景（郊区参考点）空气温度 | °C |
| `Ta` | 冠层空气温度（初始值，由模型迭代更新） | °C |
| `Tg` | 加权地表温度（初始值，由模型迭代更新） | °C |
| `TgGround` | 硬质地面表面温度（初始值，由模型迭代更新） | °C |
| `TgLand` | 土壤/植被表面温度（初始值，由模型迭代更新） | °C |
| `TgWater` | 水体表面温度（初始值，由模型迭代更新） | °C |
| `Velocity` | 参考高度（10 m）处风速 | m/s |
| `RadTotal` | 水平面总太阳辐射照度 | W/m² |
| `RadDiffuse` | 水平面散射辐射照度 | W/m² |
| `Rh` | 相对湿度 | — (0–1) |
| `Pressure` | 大气压力 | Pa |

**场地信息（`SiteInfo`）** 包含描述城市网格物理属性的静态参数：

| 字段名 | 含义 | 单位 |
| :--- | :--- | :--- |
| `TotalArea` | 网格总面积 | m² |
| `AreaBuilding` | 建筑占地面积 | m²（内部归一化为比例） |
| `AreaRoad` | 道路面积 | m²（内部归一化为比例） |
| `AreaGround` | 硬质地面面积 | m²（内部归一化为比例） |
| `AreaLandscape` | 绿化/植被面积 | m²（内部归一化为比例） |
| `AreaWater` | 水体面积 | m²（内部归一化为比例） |
| `AreaFacade` | 建筑立面面积比（立面面积/总面积） | — |
| `SVF` | 天空角系数（Sky View Factor） | — (0–1) |
| `plotRatio` | 容积率 | — |
| `greenRoofPercentage` | 绿色屋顶比例 | — |
| `groundInfiltratePercentage` | 地面渗透比例 | — |
| `kS` | 城市冠层风速衰减系数 | — |
| `coolingLoad` | 逐时建筑冷负荷（人为热源之一） | W/m²（24小时数组） |

在数值实现中，建筑、道路和硬质地面在热平衡计算中被合并为统一的"硬地"（`ground`）类别，绿化/植被归为"土地"（`land`）类别，水体单独处理（`water`）。三类下垫面的面积比之和为1，共同描述网格的地表热力学特征。

### 1.2 整体计算流程与预热机制

由于模型中地表温度 $T_g$ 和冠层空气温度 $T_a$ 的逐时求解均依赖前一小时的状态量（$T_g^{(t-1)}$、$T_a^{(t-1)}$），初始时刻（$t = 0$）的计算结果对初始状态的设定高度敏感。若直接以任意初始值启动，$t = 0$ 时刻的地表温度和空气温度将偏离物理合理范围，导致全天模拟结果失真。

为消除初始状态的不确定性，模型采用**循环预热（Preheat）机制**：在正式输出结果之前，以相同的24小时气象输入重复运行若干个完整日周期（默认2轮，可通过 `--preheat` 参数调整为1–3轮）。每轮预热结束后，第23时的 $T_g$ 和 $T_a$ 状态自动作为下一轮第0时的初始条件。经过2–3轮预热后，日周期内的温度状态趋于稳定，第23时的温度与第0时的初始条件之间的差异收敛至可接受范围，从而确保正式计算轮次的结果具有物理一致性。整个计算流程如下：

> **预热轮次1**（$t = 0 \to 23$）：以默认初始温度启动，循环更新 $T_g$ 和 $T_a$，第23时状态传递给下一轮。
>
> **预热轮次2**（$t = 0 \to 23$）：以预热轮次1第23时状态为初始条件，重复24小时模拟，第23时状态传递给正式计算轮次。
>
> **正式计算轮次**（$t = 0 \to 23$）：以预热最终状态为初始条件，完成24小时逐时求解，输出所有结果。

### 1.3 逐时求解逻辑

在每个计算轮次（预热或正式）的每个小时 $t$ 中，模型按以下顺序完成求解：

**第一步：计算热流密度驱动力（第2章）。** 根据当前小时的气象输入，分别计算四项热流密度：短波辐射得热 $q_{sw}$（`QS`）、长波辐射散热 $q_{lw}$（`QL`）、蒸发潜热 $q_v$（`QV`）和人为活动排热 $q_a$（`QA`）。其中，$q_{sw}$ 和 $q_{lw}$ 针对三类下垫面（`ground`、`land`、`water`）分别计算，合并为地表热平衡的辐射净强迫 $I_w = q_{sw} - q_{lw}$，以字典形式传入地气热交换求解器（`srfHeatFlux`）。$q_v$ 和 $q_a$ 则合并为冠层空气的热源项 $Q_{air} = q_v + q_a$，传入求解器（`airHeatFlux`）。

**第二步：耦合求解地表温度与冠层空气温度（第3章）。** 调用 `solveHeatBalance(t, srfHeatFlux, airHeatFlux, climate, site, settings)`，通过外层二分法（求解 $T_a$）与内层二分法（求解各下垫面 $T_g$）的嵌套策略，求解耦合方程组。外层二分法以平衡方程 $f_2 = 0$ 的绝对值最小化为目标求解 $T_a$；内层二分法则在给定 $T_a$ 下，分别对三类下垫面以 $f_1 = 0$ 的绝对值最小化为目标求解各自的 $T_g$，再通过面积加权得到当前小时的加权 $T_a$ 和 $T_g$，直至收敛（$|f_2| < 10^{-4}\ \text{K}$）。

**第三步：更新状态并写入结果。** 将收敛后的 $T_a^{(t)}$、$T_g^{(t)}$ 以及三类下垫面的单独温度（`TgGround`、`TgLand`、`TgWater`）写回 `ClimateInfo` 对象，供下一小时（$t+1$）的求解使用。

### 1.4 模型输出

正式计算轮次完成后，模型输出0–23时的逐时结果，分为**主要温度输出**和**中间过程量**两类。

**主要温度输出：**

| 字段名 | 含义 | 单位 |
| :--- | :--- | :--- |
| `Ta` | 网格加权冠层空气温度 | °C |
| `Tg` | 网格加权地表温度 | °C |
| `TgGround` | 硬质地面（含建筑、道路）表面温度 | °C |
| `TgLand` | 土壤/植被表面温度 | °C |
| `TgWater` | 水体表面温度 | °C |

**中间过程量（热流密度及蓄热项）：**

| 字段名 | 含义 | 单位 |
| :--- | :--- | :--- |
| `shortWaveRadHeatIntensity`（`QS`） | 短波辐射得热热流密度 | W/m² |
| `longWaveRadHeatIntensity`（`QL`） | 长波辐射散热热流密度 | W/m² |
| `vaporHeatFlowIntensity`（`QV`） | 蒸发潜热热流密度 | W/m² |
| `activityHeatFlowIntensity`（`QA`） | 人为活动排热热流密度 | W/m² |
| 地层蓄热量 | 各下垫面地层蓄热通量 $Q_{ground}$（式64） | W/m² |
| 冠层蓄热量 | 冠层空气蓄热通量 $Q_{storage,air}$（式69） | W/m² |
| 感热通量 | 地表向冠层空气的感热对流 $Q_{conv}$（式56） | W/m² |
| 深层导热通量 | 地表向深地层的热传导 $G$（式62） | W/m² |

上述中间过程量支持对模型能量平衡的完整诊断，可用于验证各热流分量的量级合理性，以及识别不同气象条件或城市形态下的主导热力学过程。


## 2. 热流密度计算方法

### 2.1 短波辐射得热热流密度计算方法

在城市热岛效应评估中，短波辐射得热是地表能量平衡的核心驱动力之一。根据《城市居住区热环境设计标准》（JGJ 286-2013）附录B中CTTC（Comprehensive Thermal Time Constant，综合热时间常数）模型的相关计算方法，本模型将太阳短波辐射的得热过程参数化为地表净吸收的短波热流密度 $q_{sw}$。

CTTC模型原用于计算气温温升，本方法对其进行物理量转换，直接求解地表吸收的热流密度。地表短波辐射得热热流密度 $q_{sw}$（单位：$\text{W/m}^2$）的计算公式为：

$$q_{sw} = \rho \cdot I_s(\tau) \tag{1}$$

其中，$\rho$ 为网格内各类下垫面的综合太阳辐射吸收系数；$I_s(\tau)$ 为考虑城市形态遮挡和植被透射后的地表实际入射太阳短波辐射照度（$\text{W/m}^2$）。

**（a）综合太阳辐射吸收系数 $\rho$**

综合太阳辐射吸收系数由网格内不同下垫面（建筑面、地面、植被面、水面）的面积占比加权求得：

$$\rho = \frac{F_J \cdot \rho_J + F_C \cdot \rho_C + F_L \cdot \rho_L + F_S \cdot \rho_S}{F_J + F_C + F_L + F_S} \tag{2}$$

其中，$F_J, F_C, F_L, F_S$ 分别代表建筑面、地面、植被面和水面的投影面积（$\text{m}^2$）；$\rho_J, \rho_C, \rho_L, \rho_S$ 为对应下垫面的太阳辐射吸收系数。根据JGJ 286-2013规范推荐，浅色和深色涂料墙面的吸收系数分别取 $0.74$ 和 $0.89$，混凝土铺装取 $0.80$，沥青取 $0.78$，水面取 $0.96$，绿化植被取 $0.72$。

**（b）实际入射太阳短波辐射照度 $I_s(\tau)$**

实际到达地表的短波辐射受建筑遮挡和植被冠层透射的影响，其计算需对无遮挡水平总辐射进行修正：

$$I_s(\tau) = \left[ \frac{(I_t(\tau) - I_{dif}(\tau)) \cdot (1 - f_{BSK}(\tau))}{h_t(\tau)} \cdot \varphi_{SVF} \right] \cdot [1 - f_Q \cdot (1 - SRT_L)] \cdot [1 - f_P \cdot (1 - SRT_C)] \tag{3}$$

其中：
- $I_t(\tau)$ 与 $I_{dif}(\tau)$ 分别为无遮挡水平面的总辐射照度与散射辐射照度（$\text{W/m}^2$）；
- $f_{BSK}(\tau)$ 为直射辐射的遮挡系数；
- $h_t(\tau)$ 为太阳高度角相关参数；
- $\varphi_{SVF}$ 为地面对天空的平均天空角系数（Sky View Factor, SVF），反映城市街谷的深浅程度；
- $f_Q$ 和 $f_P$ 分别为建筑遮阳面积比和植被遮阳面积比；
- $SRT_L$ 和 $SRT_C$ 分别为建筑遮阳构件和植被冠层的综合太阳辐射透射率。

通过上述公式，本模型能够精细化反映城市三维形态特征（如SVF、遮阳比）以及表面材质属性（如反照率、透射率）对地表短波得热的非线性调制作用。

此外，CTTC模型通过不同下垫面的热时间常数(CTTC)表达短波辐射的热惯性。在本模块中，由于热平衡计算中可以通过地层、冠层的蓄热量来精确模拟蓄热，但地表构筑物（如草皮、土层、建筑物）等的蓄热并没有表达在地层蓄热当中。其中，以建筑物为代表的蓄热体不仅蓄热量大、其散热过程与地层独立且保温性能良好，因此仍需要采用CTTC热时间常数进行表达。因此，本模型将CTTC数纳入待校准参数中，并在短波辐射模块着重体现地表构筑物的热惯性：

$$q_{sw} = \rho \cdot \sum_{i=0}^{i=\tau} [I_s(\tau) - I_s(\tau-1)] \cdot [1-exp(\frac{i-\tau}{CTTC})] \tag{1}$$

其中CTTC常数按照三类下垫面进行计算，并将三类下垫面的常数$CTTC_c$,$CTTC_l$,$CTTC_s$纳入校准：

$$CTTC = \sum_{a\in(C,L,S)}\frac{F_a}{\sum F}\cdot CTTC_a$$

### 2.2 长波辐射散热热流密度计算方法

长波辐射是地表向大气及外太空排散热量的重要途径，在夜间尤其主导了城市热岛的发展过程。参照JGJ 286-2013规范中长波辐射温降的物理机制，本模型将其转化为地表向天空的长波辐射净散热热流密度 $q_{lw}$（单位：$\text{W/m}^2$）。

长波辐射散热热流密度 $q_{lw}$ 的计算基于斯忒藩-玻尔兹曼定律（Stefan-Boltzmann Law），并结合城市形态的天空角系数进行修正：

$$q_{lw} = \sigma \cdot \left[ T_g^4 - T_{sky}^4 \right] \cdot \varphi_{SVF} \tag{4}$$

其中，$\sigma = 5.67 \times 10^{-8}\ \text{W/(m}^2\text{·K}^4\text{)}$ 为斯忒藩-玻尔兹曼常数；$T_g$ 为地表热力学温度（$\text{K}$）；$T_{sky}$ 为天空等效辐射温度（$\text{K}$）；$\varphi_{SVF}$ 为天空角系数。

**天空等效辐射温度 $T_{sky}$ 的计算**

天空等效辐射温度与近地面空气温度及大气长波辐射发射率密切相关。根据规范中大气辐射系数 $B_s(\tau)$ 的定义，$T_{sky}$ 可参数化为：

$$T_{sky} = T_a \cdot [B_s(\tau)]^{1/4} \tag{5}$$

其中，$T_a$ 为参考高度处的冠层空气热力学温度（$\text{K}$）。大气辐射系数 $B_s(\tau)$ 采用 Brunt 经验公式形式，考虑了空气湿度的影响：

$$B_s(\tau) = 0.605 + 0.048 \cdot \sqrt{\frac{P_{s}(\tau)}{1000}} \tag{6}$$

其中，$P_s(\tau)$ 为当前空气温度下的饱和水汽压（$\text{Pa}$），同样使用**Tetens 饱和蒸气压公式**

$$e_s(T) = 0.6108 \exp\!\left(\frac{17.27\,T}{T + 237.3}\right) \quad [\text{kPa}]$$

通过式 (4) 至式 (6) 以及 Tetens 公式，本模型在计算长波散热时，既考虑了城市峡谷因天空视角受限导致的辐射截留效应（即 $\varphi_{SVF} < 1$ 带来的散热削弱），又反映了大气湿度增加对天空有效辐射温度的提升作用。这一参数化方案在保留规范物理内核的同时，将其无缝整合至以热流密度为驱动的地表能量平衡方程中。

### 2.3 基于 FAO-56 Penman–Monteith 方程的逐小时蒸发热计算

### 2.3.1 模型框架

本研究采用 Penman–Monteith 组合方程计算水面、植被和渗透型铺装的逐小时蒸发潜热通量。该方法同时考虑下垫面的可用能量、空气水汽压亏缺、湍流输运能力和表面供水阻力。FAO-56 将 Penman–Monteith 方程作为参考作物蒸散计算的标准方法；本研究保留其通用阻力形式，并针对城市不同下垫面设置相应的可用能量和表面阻力参数。

第 \(i\) 类下垫面的蒸发潜热通量为：

$$
\boxed{
Q_{V,i}^{+}
=
\frac{
\Delta A_i
+
\rho_a c_p\dfrac{\mathrm{VPD}}{r_{a,i}}
}{
\Delta
+
\gamma\left(
1+\dfrac{r_{s,i}}{r_{a,i}}
\right)
}
}
\tag{7}
$$

其中：

$$
i\in\{\mathrm{water},\mathrm{veg},\mathrm{pave}\}
$$

\(Q_{V,i}^{+}\) 表示蒸发从下垫面带走的潜热通量大小，单位为：

$$
Q_{V,i}^{+}\quad[\mathrm{W/m^2}]
$$

若模型规定进入地表系统的热量为正，则蒸发冷却项应以负值写入地表热平衡：

$$
\boxed{Q_{V,i}=-Q_{V,i}^{+}}
\tag{8}
$$

式（7）的分子由能量驱动项和空气动力驱动项组成：

$$
\underbrace{\Delta A_i}_{\text{能量驱动项}}
+
\underbrace{\rho_a c_p\frac{\mathrm{VPD}}{r_{a,i}}}_{\text{空气动力驱动项}}
\tag{9}
$$

分母表示饱和水汽压对温度的响应，以及空气动力阻力和表面阻力对实际蒸发过程的共同限制：

$$
\Delta+\gamma\left(1+\frac{r_{s,i}}{r_{a,i}}\right)
\tag{10}
$$

### 2.3.2 可用能量

标准 Penman–Monteith 方程中的可用能量为：

$$
A_i=R_{n,i}-G_i
\tag{11}
$$

其中 \(R_{n,i}\) 为净辐射，\(G_i\) 为进入下垫面内部的热通量。由于本研究的短波辐射模块已采用城市热时间系数 CTTC 对辐射得热的热惯性和时间延迟进行简化表达，蒸发模块不再额外计算完整的地表储热项，以避免与后续热平衡中的储热过程重复。

因此，本研究将第 \(i\) 类下垫面的可用能量定义为：

$$
\boxed{A_i=Q_{S,\mathrm{CTTC},i}+Q_{L,i}+f_{A,i}Q_A}
\tag{12}
$$

其中：

- \(Q_{S,\mathrm{CTTC},i}\) 为短波辐射模块输出的 CTTC 修正吸收短波热流密度，单位为 \(\mathrm{W/m^2}\)；
- \(Q_{L,i}\) 为第 \(i\) 类下垫面的净长波热流密度，单位为 \(\mathrm{W/m^2}\)；
- \(Q_A\) 为人为废热热流密度，单位为 \(\mathrm{W/m^2}\)；
- \(f_{A,i}\) 为人为废热中直接作用于第 \(i\) 类下垫面的比例。

若人为废热主要释放至城市冠层空气，而不是直接作用于蒸发表面，则取：

$$
f_{A,i}=0
\tag{13}
$$

此时：

$$
\boxed{A_i=Q_{S,\mathrm{CTTC},i}+Q_{L,i}}
\tag{14}
$$

本研究规定下垫面获得能量为正，向外损失能量为负。因此，净长波散热通常表现为 \(Q_{L,i}<0\)。夜间 \(A_i\) 可以为负。负可用能量会抑制蒸发，但空气动力项仍可能维持有限的夜间蒸发。

### 2.3.3 空气水汽状态

饱和水汽压采用 Tetens 公式计算：

$$
\boxed{
e_s(T)
=
0.6108
\exp\left(
\frac{17.27T}{T+237.3}
\right)
}
\qquad[\mathrm{kPa}]
\tag{15}
$$

空气实际水汽压为：

$$
\boxed{e_a=\frac{RH}{100}e_s(T_a)}
\qquad[\mathrm{kPa}]
\tag{16}
$$

空气水汽压亏缺为：

$$
\boxed{\mathrm{VPD}=\max[e_s(T_a)-e_a,0]}
\qquad[\mathrm{kPa}]
\tag{17}
$$

采用式（7）后，不再以 \(e_s(T_g)-e_a\) 作为空气动力项的水汽压差。地表温度对蒸发的影响已通过净长波、可用能量和地表热平衡体现，继续采用地表饱和水汽压可能造成重复表达。

饱和水汽压曲线斜率为：

$$
\boxed{
\Delta
=
\frac{4098e_s(T_a)}{(T_a+237.3)^2}
}
\qquad[\mathrm{kPa/K}]
\tag{18}
$$

湿度计常数为：

$$
\boxed{\gamma=\frac{c_pP}{\varepsilon\lambda_v}}
\tag{19}
$$

若 \(P\) 使用 kPa，可采用：

$$
\boxed{\gamma=0.000665P}
\qquad[\mathrm{kPa/K}]
\tag{20}
$$

其中 \(\varepsilon=0.622\)。

### 2.3.4 空气热物性

空气密度采用：

$$
\boxed{\rho_a=\frac{P_{\mathrm{Pa}}}{R_d(T_a+273.15)}}
\tag{21}
$$

其中 \(R_d=287.05\ \mathrm{J/(kg\cdot K)}\)。空气定压比热容取：

$$
\boxed{c_p=1005\ \mathrm{J/(kg\cdot K)}}
\tag{22}
$$

水的汽化潜热可按空气温度修正：

$$
\boxed{\lambda_v=(2.501-0.002361T_a)\times10^6}
\qquad[\mathrm{J/kg}]
\tag{23}
$$

也可在研究温度范围较窄时取：

$$
\lambda_v=2.45\times10^6\ \mathrm{J/kg}
\tag{24}
$$

在本研究中，\(\lambda_v\) 主要用于湿度计常数和铺装储水更新。最终输出直接为潜热通量 \(Q_V^+\)，不再换算为蒸发质量通量。

### 2.3.5 空气动力阻力

空气动力阻力 \(r_{a,i}\) 表示水汽由下垫面输送至参考空气层的湍流交换阻力：

$$
\boxed{r_{a,i}=\frac{1}{C_{H,i}v_i}}
\tag{25}
$$

其中 \(C_{H,i}\) 为体积交换系数，\(v_i\) 为局部风速。体积交换系数为：

$$
\boxed{C_{H,i}=\frac{\kappa^2}{\ln^2(z_{\mathrm{ref}}/z_{0,i})}}
\tag{26}
$$

其中 \(\kappa=0.4\)。在统一简化参数下，可取：

$$
z_{\mathrm{ref}}=1.5\ \mathrm m,\qquad z_0=0.005\ \mathrm m
\tag{27}
$$

此时：

$$
C_H\approx0.00492
\tag{28}
$$

三类下垫面采用相同局部风速和等效粗糙度：

$$
\boxed{r_{a,\mathrm{water}}=r_{a,\mathrm{veg}}=r_{a,\mathrm{pave}}=\frac{1}{C_Hv_{1.5}}}
\tag{29}
$$

此外，阻力项rs则作为待校准的经验参数给入计算。默认值$r_{s,water}=0,r_{s,land}=100,r_{s,ground}=200$


## 3. 地气热交换参数化方法

### 3.1 模型框架与能量平衡方程

传统城市热岛模型通常采用线性参数化方案，将地表与空气之间的热交换系数设定为与风速线性相关的固定值，或直接将地表温度作为已知输入。这类简化处理在极端气象条件下（如近静风、强日照）往往导致物理上不合理的结果，且无法捕捉城市冠层内部的热力学非线性特征。

本研究提出一套物理机制更为完整的参数化框架，将地气热交换分解为强制对流、自然对流、分子导热下限约束、地层深层导热和地层蓄热五个独立过程。

**特别需要指出的是，在能量平衡的耦合计算中，模型根据物理过程的作用路径对热流密度进行了明确分组：**
*   **地表热平衡驱动力（`srfHeatFlux`）**：短波辐射得热和长波辐射散热直接作用于下垫面，作为地表能量平衡的辐射强迫输入。
*   **冠层空气热平衡驱动力（`airHeatFlux`）**：蒸发潜热（消耗地表能量并影响空气湿度/温度）和人为活动排热直接作用于冠层空气，作为空气能量平衡的附加源汇项。

通过联立方程组的形式，模型实现 $T_g$ 和 $T_a$ 的同步求解，从而避免将地表温度作为直接输入参数。

地表能量平衡方程描述了地表温度 $T_g$ 的决定机制：地表净得热（辐射强迫 $I_w$）等于地表向冠层空气的感热对流、向深地层的热传导以及地层蓄热之和：

$$I_w = Q_{conv} + G + Q_{ground} \tag{54}$$

其中 $I_w = q_{sw} - q_{lw}$（单位：$\text{W/m}^2$）为地表净辐射热流密度，由第2章计算得出。

冠层空气能量平衡方程描述了冠层空气温度 $T_a$ 的决定机制：地表向冠层空气的感热输入，加上人为热和蒸发潜热（`airHeatFlux`），等于冠层通风散热与冠层空气蓄热之和：

$$Q_{conv} + Q_{air} = Q_{vent} + Q_{storage,air} \tag{55}$$

其中 $Q_{air} = q_v + q_a$ 为蒸发潜热和人为热之和（作用于冠层空气的热流密度）。

### 3.2 地气对流换热参数化

地表与冠层空气之间的感热通量 $Q_{conv}$ 采用牛顿冷却定律参数化：

$$Q_{conv} = \alpha_{conv} \cdot (T_g - T_a) \tag{56}$$

其中 $\alpha_{conv}$（$\text{W/(m}^2\text{·K)}$）为综合对流换热系数，由强制对流、自然对流和分子导热下限三项合成（Churchill & Usagi, 1972）：

$$\alpha_{conv} = \max\!\left(\sqrt{\alpha_{forced}^2 + \alpha_{nat}^2},\ \alpha_{min}\right) \tag{57}$$

**（a）强制对流换热系数 $\alpha_{forced}$**

强制对流换热系数基于 Monin-Obukhov 相似理论（中性大气稳定度近似）推导。体传热系数 $C_H$ 由空气动力学粗糙度长度 $a_r$ 确定（Stull, 1988）：

$$C_H = \frac{\kappa^2}{\left[\ln\!\left(\dfrac{z_{ref}}{a_r}\right)\right]^2} \tag{58}$$

其中 $\kappa = 0.40$ 为冯·卡门常数，$z_{ref} = 10\ \text{m}$ 为参考风速测量高度，$a_r = 0.22\ \text{m}$ 为城市环境典型空气动力学粗糙度长度（由模型其他模块内置提供）。强制对流换热系数为：

$$\alpha_{forced} = \frac{P}{R_d T_a} \cdot c_p \cdot C_H \cdot v_t \tag{59}$$

其中 $P$ 为大气压力（Pa），$R_d = 287.05\ \text{J/(kg·K)}$ 为干空气气体常数，$c_p = 1005\ \text{J/(kg·K)}$ 为空气定压比热，$v_t$ 为参考高度处的无遮挡原始风速（m/s）。式 (59) 中的空气密度 $\rho = P/(R_d T_a)$ 随冠层温度 $T_a$ 动态更新。

**（b）自然对流换热系数 $\alpha_{nat}$**

自然对流（浮力驱动）换热系数采用湍流自然对流的经典幂次律参数化（Awbi, 1998；Walton, 1983）：

$$\alpha_{nat} = c_1 \cdot |T_g - T_a|^{1/3} \tag{60}$$

其中 $c_1$ 为待校准的自然对流相关系数，量纲为 $\text{W/(m}^2\text{·K}^{4/3}\text{)}$。该形式对应湍流自然对流的 $Nu \propto Ra^{1/3}$ 渐近律，在城市地表的典型温差范围（$1$–$20\ \text{K}$）内具有良好的物理一致性。

$c_1$ 的推荐默认值为 $1.31\ \text{W/(m}^2\text{·K}^{4/3}\text{)}$，来源于两条收敛的文献依据：EnergyPlus TARP 算法（Walton, 1983）对大型粗糙水平加热表面的参数化给出 $h_n = 1.31|\Delta T|^{1/3}$，该值同时被 ISO 15099 标准和 ASHRAE Handbook of Fundamentals 所采用；MoWiTT 模型（Yazdanian & Klems, 1994）对光滑垂直表面给出 $C_t = 0.84$，构成城市粗糙水平地面的下限参考。城市地面为粗糙近水平面，其自然对流强度高于光滑垂直面，因此取 $c_1 = 1.31$ 作为默认值，校准范围建议为 $[0.84, 2.0]$。

**（c）分子导热下限 $\alpha_{min}$**

当近地面风速趋近于零（净风条件）且地气温差趋近于零时，$\alpha_{forced} \to 0$ 且 $\alpha_{nat} \to 0$，方程组的 Jacobian 矩阵退化，导致迭代求解出现数值奇点。为此，引入与温差和风速均无关的换热系数绝对下限：

$$\alpha_{min} = \frac{k_{air}}{\delta_{still}} \tag{61}$$

其中 $k_{air} \approx 0.026\ \text{W/(m·K)}$ 为空气导热率，$\delta_{still}$ 为静止空气等效热边界层厚度。$\alpha_{min}$ 的物理意义是：即使在完全无风且地气温差为零的极端条件下，地表与空气之间仍然存在一层薄的分子导热层（粘性底层），其等效换热系数不可能降至零。

取 $\delta_{still} = 3\ \text{mm}$，则 $\alpha_{min} = 0.026/0.003 \approx 8.7\ \text{W/(m}^2\text{·K)}$。该值与 ISO 6946（2017）规定的室内静止空气表面对流换热系数（约 $7.7\ \text{W/(m}^2\text{·K)}$）以及 EnergyPlus ASHRAE 简化模型的静止空气下限（约 $5\ \text{W/(m}^2\text{·K)}$）量级一致（Walton, 1983）。式 (57) 中的 $\max$ 算子保证了在任何气象条件下 $\alpha_{conv} \geq \alpha_{min} > 0$，从而消除数值奇点，同时不影响正常风速条件下方程组的解。

### 3.3 深层导热通量

地表向深地层恒温边界的热传导通量采用等效稳态导热模型：

$$G = U_{d,grid} \cdot (T_g - T_0) \tag{62}$$

其中 $T_0$ 为深地层恒温层温度（通常取年平均地温），$U_{d,grid}$ 为网格尺度的等效深层导热系数（$\text{W/(m}^2\text{·K)}$），由三类下垫面按面积比加权得出：

$$U_{d,grid} = F_{land} \cdot U_{d,land} + F_{ground} \cdot U_{d,ground} + F_{water} \cdot U_{d,water} \tag{63}$$

其中 $F_{land}$、$F_{ground}$、$F_{water}$ 分别为植被、硬地、水体的面积比（三者之和为 1）。$U_{d,i}$ 的物理含义是地表到深地层恒温边界之间的等效稳态热通道，可表示为 $U_{d,i} = k_{eff,i}/L_{eff,i}$，其中 $k_{eff,i}$ 为等效导热率，$L_{eff,i}$ 为等效热阻层厚度。

需要特别指出的是，$U_{d,i}$ 不等同于材料导热率，也不应与动态热导纳（thermal admittance）混淆。它描述的是地表温度与深层恒温边界之间的缓慢稳态热通道，而非地表材料的即时导热能力。各类下垫面的推荐默认值及其物理依据如表 1 所示。

**表 1. 等效深层导热系数默认值**

| 下垫面类型 | 符号 | 默认值（$\text{W/(m}^2\text{·K)}$） | 推荐范围 | 物理依据 |
| :--- | :--- | :--- | :--- | :--- |
| 植被/土壤 | $U_{d,land}$ | 0.40 | 0.20–0.80 | 土壤 $k_{eff} \approx 0.4$–$0.8\ \text{W/(m·K)}$，$L_{eff} \approx 1$–$2\ \text{m}$ |
| 硬地/铺装 | $U_{d,ground}$ | 0.70 | 0.30–1.00 | 沥青/混凝土 $k_{eff} \approx 1.0$–$1.5\ \text{W/(m·K)}$，$L_{eff} \approx 1$–$2\ \text{m}$ |
| 水体 | $U_{d,water}$ | 1.00 | 0.00–2.00 | 水体热调节主要由热容量承担，深层导热通道极弱 |

### 3.4 地层蓄热通量

地表材料（土壤、铺装、水体）的热容量效应通过蓄热通量项描述：

$$Q_{ground} = C_{grid} \cdot \frac{T_g^{(n)} - T_g^{(n-1)}}{\Delta t} \tag{64}$$

其中 $\Delta t$ 为时间步长，$T_g^{(n-1)}$ 为上一时间步的地表温度，$C_{grid}$ 为网格尺度的单位面积有效热容量（$\text{J/(m}^2\text{·K)}$）：

$$C_{grid} = F_{land} \cdot C_{land} + F_{ground} \cdot C_{ground} + F_{water} \cdot C_{water} \tag{65}$$

$C_i = \rho_i c_i z_{eff,i}$ 为各类下垫面参与日周期储热的等效热容，其中 $\rho_i$ 为材料密度，$c_i$ 为比热容，$z_{eff,i}$ 为有效储热深度。水体的有效热容量由有效混合水深 $h_w$ 确定：

$$C_{water} = \rho_w c_w h_w \tag{66}$$

其中 $\rho_w = 1000\ \text{kg/m}^3$，$c_w = 4186\ \text{J/(kg·K)}$，默认取 $h_w = 0.5\ \text{m}$，对应 $C_{water} \approx 2.1 \times 10^6\ \text{J/(m}^2\text{·K)}$。各类下垫面有效热容量的推荐默认值见表 2。

**表 2. 单位面积有效热容量默认值**

| 下垫面类型 | 符号 | 默认值（$\text{J/(m}^2\text{·K)}$） | 推荐范围 | 物理依据 |
| :--- | :--- | :--- | :--- | :--- |
| 植被/土壤 | $C_{land}$ | $1.0 \times 10^6$ | $0.5$–$2.0 \times 10^6$ | $\rho_s \approx 1500\ \text{kg/m}^3$，$c_s \approx 800\ \text{J/(kg·K)}$，$z_{eff} \approx 0.8\ \text{m}$ |
| 硬地/铺装 | $C_{ground}$ | $2.0 \times 10^6$ | $1.0$–$3.0 \times 10^6$ | $\rho \approx 2000\ \text{kg/m}^3$，$c \approx 900\ \text{J/(kg·K)}$，$z_{eff} \approx 0.1$–$0.15\ \text{m}$ |
| 水体 | $C_{water}$ | $2.1 \times 10^6$ | $\rho_w c_w h_w$ | 默认有效混合水深 $h_w = 0.5\ \text{m}$ |

引入地层蓄热项的物理意义在于：白天 $I_w > 0$ 时，$Q_{ground} > 0$，部分热量被地表材料吸收储存，从而减缓 $T_g$ 的上升，避免了无蓄热模型中因过度约束导致 $T_g < T_a$ 的非物理现象；夜间 $I_w < 0$ 时，$Q_{ground} < 0$，储热释放，形成城市热岛的热滞后效应（Oke et al., 2017）。

### 3.5 冠层通风散热与空气蓄热

冠层空气的通风散热通量 $Q_{vent}$ 描述冠层空气与上风向背景大气之间的热量交换：

$$Q_{vent} = \alpha_{vent} \cdot (T_a - T_{a,bg}) \tag{67}$$

$$\alpha_{vent} = \frac{P}{R_d T_a} \cdot c_p \cdot v_{1.5} \tag{68}$$

其中 $v_{1.5}$ 为经城市形态修正后的近地面（1.5 m 高度）拟合风速，由模型的城市形态模块计算提供；$T_{a,bg}$ 为背景空气温度（上风向参考点或郊区气象站观测值）。$\alpha_{vent}$ 的物理意义是单位时间内被风替换的单位面积冠层空气所携带的热量，量纲为 $\text{W/(m}^2\text{·K)}$。

冠层空气的蓄热通量 $Q_{storage,air}$ 描述冠层空气柱本身的热惯量：

$$Q_{storage,air} = \frac{P}{R_d T_a} \cdot c_p \cdot H_{air} \cdot \frac{T_a^{(n)} - T_a^{(n-1)}}{\Delta t} \tag{69}$$

其中 $H_{air}$ 为冠层高度（m），由模型默认提供。稳态计算时，令 $Q_{storage,air} = 0$。

### 3.6 完整方程组

将式 (54)–(69) 代入能量平衡方程，得到以 $T_g$ 和 $T_a$ 为未知量的非线性方程组：

$$\begin{cases}
f_1(T_g,\, T_a) = \alpha_{conv}(T_g, T_a)\cdot(T_g-T_a) + U_{d,grid}(T_g-T_0) + C_{grid}\dfrac{T_g^{(n)} - T_g^{(n-1)}}{\Delta t} - I_w = 0 \\[10pt]
f_2(T_g,\, T_a) = \alpha_{conv}(T_g, T_a)\cdot(T_g-T_a) + Q_{air} - \alpha_{vent}(T_a)\cdot(T_a-T_{a,bg}) - \dfrac{P}{R_d T_a} c_p H_{air} \dfrac{T_a^{(n)}-T_a^{(n-1)}}{\Delta t} = 0
\end{cases} \tag{70}$$

其中 $\alpha_{conv}(T_g, T_a) = \max\!\left(\sqrt{[c_1|T_g-T_a|^{1/3}]^2 + \left[\frac{P}{R_d T_a}c_p C_H v_t\right]^2},\ \frac{k_{air}}{\delta_{still}}\right)$。

稳态模式下，令 $C_{grid} = 0$ 且 $H_{air}/\Delta t = 0$，方程组退化为仅含深层导热和通风散热的简化形式。

### 3.7 模型输入参数体系

模型的输入参数分为三类：可直接观测的气象参量、由城市形态计算得出的参量，以及具有通用经验值的设定参数。

**可观测气象参量**（模型驱动量）：

| 符号 | 含义 | 单位 |
| :--- | :--- | :--- |
| $I_w$ | 地表总净得热热流密度 | $\text{W/m}^2$ |
| $T_{a,bg}$ | 背景空气温度（参考点） | $\text{K}$ |
| $T_0$ | 深地层恒温层温度 | $\text{K}$ |
| $P$ | 大气压力 | $\text{Pa}$ |
| $v_t$ | 参考高度（10 m）处无遮挡风速 | $\text{m/s}$ |

**城市形态计算参量**：

| 符号 | 含义 | 单位 |
| :--- | :--- | :--- |
| $v_{1.5}$ | 近地面（1.5 m）修正风速 | $\text{m/s}$ |
| $F_{land},\, F_{ground},\, F_{water}$ | 植被、硬地、水体面积比 | — |
| $H_{air}$ | 冠层高度 | $\text{m}$ |
| $a_r$ | 空气动力学粗糙度长度 | $\text{m}$ |

**经验设定参数**（含默认值，见表 3）：

**表 3. 模型经验参数汇总**

| 类别 | 符号 | 默认值 | 单位 | 来源 |
| :--- | :--- | :--- | :--- | :--- |
| 冯·卡门常数 | $\kappa$ | 0.40 | — | 通用物理常数 |
| 干空气气体常数 | $R_d$ | 287.05 | $\text{J/(kg·K)}$ | 通用物理常数 |
| 空气定压比热 | $c_p$ | 1005.0 | $\text{J/(kg·K)}$ | 通用物理常数 |
| 参考高度 | $z_{ref}$ | 10.0 | $\text{m}$ | 气象站标准 |
| 植被深层导热系数 | $U_{d,land}$ | 0.40 | $\text{W/(m}^2\text{·K)}$ | Farouki (1981); Masson (2000) |
| 硬地深层导热系数 | $U_{d,ground}$ | 0.70 | $\text{W/(m}^2\text{·K)}$ | Kusaka et al. (2001) |
| 水体深层导热系数 | $U_{d,water}$ | 1.00 | $\text{W/(m}^2\text{·K)}$ | 本研究修正值 |
| 植被有效热容量 | $C_{land}$ | $1.0 \times 10^6$ | $\text{J/(m}^2\text{·K)}$ | Masson (2000) |
| 硬地有效热容量 | $C_{ground}$ | $2.0 \times 10^6$ | $\text{J/(m}^2\text{·K)}$ | Kusaka et al. (2001) |
| 水体有效混合水深 | $h_w$ | 0.5 | $\text{m}$ | 本研究默认值 |
| 空气导热率 | $k_{air}$ | 0.026 | $\text{W/(m·K)}$ | Incropera & DeWitt (2007) |
| 静止空气热边界层厚度 | $\delta_{still}$ | 0.003 | $\text{m}$ | ISO 6946 (2017) 推导 |
| **自然对流系数（待校准）** | $c_1$ | **1.31** | $\text{W/(m}^2\text{·K}^{4/3}\text{)}$ | Awbi (1998); Walton (1983) |

全模型仅含一个待校准参数 $c_1$，其推荐默认值 $1.31\ \text{W/(m}^2\text{·K}^{4/3}\text{)}$ 来源于 EnergyPlus TARP 算法对粗糙水平加热表面的参数化（Walton, 1983），校准范围为 $[0.84, 2.0]$，下限对应 MoWiTT 模型中光滑垂直表面的 $C_t = 0.84$（Yazdanian & Klems, 1994）。

### 3.8 数值求解方法

方程组 (70) 为非线性方程组，由于系统具有强耦合特征，本模型中 $T_g$ 和 $T_a$ 的求解均采用**二分法**（Bisection Method）进行数值寻优。

**目标函数**：分别将地表能量平衡方程 $f_1(T_g, T_a) = 0$ 与冠层空气能量平衡方程 $f_2(T_g, T_a) = 0$ 作为目标，求解过程旨在使目标函数的绝对值最小化：

$$\min |f_1(T_g, T_a)| \quad \text{和} \quad \min |f_2(T_g, T_a)|$$

**嵌套求解策略**：

1. **外层二分法求解 $T_a$**：在合理的环境温度范围内（例如 $[-30^\circ\mathrm{C},\ 60^\circ\mathrm{C}]$）设定 $T_a$ 的初始搜索区间。对于每一个候选的 $T_a$ 值，调用内层求解器。

2. **内层二分法求解 $T_g$**：在给定的 $T_a$ 下，针对每一类下垫面，在物理合理的温度范围内（例如 $[-30^\circ\mathrm{C},\ 80^\circ\mathrm{C}]$）使用二分法寻找使 $|f_1(T_g, T_a)|$ 最小的 $T_g$。

3. **残差评估与外层迭代**：内层返回各下垫面的 $T_g$ 后，计算面积加权平均地表温度，并代入冠层空气能量平衡方程计算残差 $f_2$。根据 $f_2$ 的符号更新外层二分法的搜索区间，直至外层残差 $|f_2|$ 满足收敛精度（$10^{-4}\ \text{K}$）或达到最大迭代次数（默认 50 步）。

该全二分法嵌套求解策略不依赖于目标函数的导数信息，有效避免了极端气象条件下由于非线性对流换热项（特别是自然对流与强制对流切换时）导致 Jacobian 矩阵退化或迭代发散的问题，从而确保了模型在整个计算周期内的极高数值稳定性。

### 3.9 模型的物理一致性保证

本参数化框架通过以下机制保证物理一致性：

**（1）地表温度的物理合理性**：引入地层蓄热项 $Q_{ground}$ 后，方程 $f_1 = 0$ 中增加了 $C_{eff} T_g$ 的正项，使得在相同 $I_w$ 下求解出的 $T_g$ 更高（地层吸热减缓了地表降温）。这从根本上消除了无蓄热模型中白天 $T_g < T_a$ 的非物理现象，使模型在城市热岛的"白天储热、夜间释热"机制上具有正确的物理行为。

**（2）数值稳定性**：$\alpha_{min}$ 的引入保证了 $\alpha_{conv}$ 在任何气象条件下均有正的下界，避免了净风 + 温度接近时方程组 Jacobian 矩阵退化导致的数值发散。

**（3）参数可观测性**：模型所有驱动量均为气象站可直接观测的量（温度、风速、气压）或由城市形态计算得出的量（面积比、修正风速），不依赖难以直接测量的地表温度作为输入，从而保证了模型在实际应用中的可操作性。

---

## 参考文献

[1] 中华人民共和国住房和城乡建设部. (2013). *城市居住区热环境设计标准* (JGJ 286-2013). 中国建筑工业出版社.

[2] Monin, A. S., & Obukhov, A. M. (1954). Basic laws of turbulent mixing in the surface layer of the atmosphere. *Trudy Geofizicheskogo Instituta, Akademiya Nauk SSSR*, 24(151), 163-187.

[3] Brunt, D. (1932). Notes on radiation in the atmosphere. I. *Quarterly Journal of the Royal Meteorological Society*, 58(247), 389-420.

[4] Tetens, O. (1930). Über einige meteorologische Begriffe. *Zeitschrift für Geophysik*, 6, 297-309.

[5] Priestley, C. H. B., & Taylor, R. J. (1972). On the assessment of surface heat flux and evaporation using large-scale parameters. *Monthly Weather Review*, 100(2), 81-92.

[6] Churchill, S. W., & Usagi, R. (1972). A general expression for the correlation of rates of transfer and other phenomena. *AIChE Journal*, 18(6), 1121-1128.

[7] Deardorff, J. W. (1978). Efficient prediction of ground surface temperature and moisture, with inclusion of a layer of vegetation. *Journal of Geophysical Research*, 83(C4), 1889-1903.

[8] Penman, H. L. (1948). Natural evaporation from open water, bare soil and grass. *Proceedings of the Royal Society of London. Series A. Mathematical and Physical Sciences*, 193(1032), 120-145.

[9] Santamouris, M. (Ed.). (2001). *Energy and Climate in the Urban Built Environment*. James & James.

[10] Oke, T. R. (1987). *Boundary Layer Climates* (2nd ed.). Routledge.

[11] Stull, R. B. (1988). *An Introduction to Boundary Layer Meteorology*. Springer.

[12] Walton, G. N. (1983). *Thermal Analysis Research Program Reference Manual* (NBSSIR 83-2655). National Bureau of Standards.

[13] Yazdanian, M., & Klems, J. H. (1994). Measurement of the exterior convective film coefficient for windows in low-rise buildings. *ASHRAE Transactions*, 100(1), 1087-1096.

[14] Awbi, H. B. (1998). Calculation of convective heat transfer coefficients of room surfaces for natural convection. *Energy and Buildings*, 28(2), 219-227.

[15] Farouki, O. T. (1981). *Thermal Properties of Soils* (CRREL Monograph 81-1). U.S. Army Corps of Engineers.

[16] Kusaka, H., Kondo, H., Kikegawa, Y., & Kimura, F. (2001). A simple single-layer urban canopy model for atmospheric models: Comparison with multi-layer and infinite-layer models. *Boundary-Layer Meteorology*, 101(3), 329-358.

[17] Masson, V. (2000). A physically-based scheme for the urban energy budget in atmospheric models. *Boundary-Layer Meteorology*, 94(3), 357-397.

[18] ISO 6946:2017. *Building components and building elements — Thermal resistance and thermal transmittance — Calculation methods*. International Organization for Standardization.

[19] Incropera, F. P., & DeWitt, D. P. (2007). *Fundamentals of Heat and Mass Transfer* (6th ed.). John Wiley & Sons.

[20] Oke, T. R., Mills, G., Christen, A., & Voogt, J. A. (2017). *Urban Climates*. Cambridge University Press.

[21] Dias, N. L., et al. (2023). STAEBLE: A surface-temperature- and available-energy-based lake evaporation model. *Water Resources Research*, 59(2), e2022WR033012.

[22] Bolton, D. (1980). The computation of equivalent potential temperature. *Monthly Weather Review*, 108(7), 1046-1053.

[23] Allen, R. G., Pereira, L. S., Raes, D., & Smith, M. (1998). *Crop evapotranspiration: Guidelines for computing crop water requirements* (FAO Irrigation and Drainage Paper No. 56). FAO.

[24] Perez, P. J., et al. (2008). A simple model for estimating the Bowen ratio from climatic factors for peninsular Spain. *Agricultural and Forest Meteorology*, 148(5), 680-688.

[25] Zhou, J., et al. (2021). Mechanisms and empirical modeling of evaporation from hardened surfaces in urban areas. *International Journal of Environmental Research and Public Health*, 18(4), 1790.

[26] Tziampou, N., et al. (2020). Fluid transport within permeable pavement systems: A review of evaporation processes, moisture loss measurement and the current state of knowledge. *Construction and Building Materials*, 254, 119344.

[27] Starke, P., Göbel, P., & Coldewey, W. G. (2010). Urban evaporation rates for water-permeable pavements. *Water Science and Technology*, 62(5), 1161-1169.


