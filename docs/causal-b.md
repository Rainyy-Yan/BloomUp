# B 模块首版：审查、分层 DR-DID 与证伪

本模块在本地分析**单学期、共同处理开始时点、完整学生前后测、预设离散基线协变量**的面板。两学期应分别导入和分析，不把秋季和春季直接当作同一人的前后，也不从提问日志推断独立成绩。当前只完成合成验证，没有真实学生因果结论。

## 快速运行

```text
python -m challenge --root local_state/b_module causal-demo
python -m challenge --root local_state/b_module causal-validate --simulations 100 --replicates 200 --seed 2045
```

演示生成两份独立合成面板、审查、估计和 Markdown/HTML 报告，回执包含路径。设定的效应分别为 5 和 0；每份面板有 72 个独立簇，每簇两名合成学生，处理/对照学生构成不同。模拟诊断另含平行趋势失效情形。状态仅保存在指定目录，默认目录已被 Git 忽略。

## 输入与流程

```text
python -m challenge --root local_state/b_module causal-panel-import --file local_state/panel.json
python -m challenge --root local_state/b_module causal-audit --panel PANEL_ID --protocol configs/causal.local.json
python -m challenge --root local_state/b_module causal-report --analysis AUDIT_ID
python -m challenge --root local_state/b_module analysis-causal --audit AUDIT_ID --seed 2045 --replicates 1000
python -m challenge --root local_state/b_module causal-report --analysis EFFECT_ID
python -m challenge --root local_state/b_module verify --artifact REPORT_ID
```

所有大写 ID 用上一步回执替换。审查报告可在阻断状态生成；估计器只接受 `ready_under_assumptions` 的审查。不给 `--audit` 的旧命令继续阻断。审查输出必须完整检查，成功保存审查产物不表示它通过。报告接受审查、效应或 `causal_validation` 模拟验证产物；验证报告直接读取指定产物，不重新运行模拟。

也可通过完整流程入口避免手动串联 ID：

```text
python -m challenge --root local_state/b_module causal-run --file local_state/panel.json --protocol configs/causal.local.json --seed 2045 --replicates 1000
```

回执提供 `run`、`panel`、`audit`、`effect`、报告路径和 `analysis_status`。`blocked` 时 `effect=null`，仍生成审查报告，进程退出码 2；`estimated_without_inference` 表示有条件点估计，但簇不足、支持失败或退化导致没有区间/p值；`estimated_under_assumptions` 表示已生成条件估计和近似推断，不表示假设已被证明。后两种流程退出码为 0。`verify --artifact RUN_ID` 可递归核对全流程依赖。同内容配置复用版本 ID，导出报告文件使用新目录。

复制 `configs/causal.protocol.template.json` 为被忽略的 `configs/causal.local.json` 后，按真实证据填写。模板日期、分数范围、教育阈值与偏差界都是示例，不是赛事数据事实。模板默认缺少冻结人、证据、假设依据且独立性/可比性为 false，不能用于获得真实效应。

### 面板数据合同

JSON 外层只含 `schema_version="causal-panel-v1"`、`synthetic` 布尔值、`rows` 非空数组。一行代表一名学生，学生键在该面板内唯一；不接受逐轮长表。以下为一条合成记录示例，单独这一条记录因没有对照而无法分析：

```json
{
  "schema_version": "causal-panel-v1",
  "synthetic": true,
  "rows": [{
    "student_key": "SYNTHETIC-1",
    "cluster_id": "SYNTHETIC-CLASS-1",
    "term": "synthetic_fall",
    "treated": 1,
    "pre_score": 40,
    "post_score": 48,
    "baseline_time": "2025-09-20T00:00:00+00:00",
    "covariates_time": "2025-09-20T00:00:00+00:00",
    "treatment_time": "2025-10-01T00:00:00+00:00",
    "followup_time": "2025-11-03T00:00:00+00:00",
    "covariates": {"foundation": "low"}
  }]
}
```

- `treated` 严格为整数 0/1，不能用 true/false；`synthetic` 必须如实标识，不是绕过审查的开关。
- 所有时间要求 ISO 格式及明确时区。基线和协变量时间严格早于协议 `time_zero`；处理组启动时间等于该共同时间；对照启动时间为 null；后测位于冻结的 `followup_window`。
- `covariates` 只支持预先定义的离散字符串分类。连续变量须在看结果前明确分箱；不能把学生标识、未来行为或处理后交互深度当协变量。程序能核对声明的时间，不能鉴别伪造的时间或来源。
- `pre_score/post_score` 为有限数值或 null。null 可以导入，但会阻断当前估计，不做静默完整个案筛选或填补。分数须处于协议范围，首版只支持“高分更好”。
- `cluster_id` 表示实际分配/依赖单元：例如按班级引入 AI 时使用班级。首版要求每簇处理状态一致；若同一班级混合分配，应另设计合适推断方案，不能伪拆班级键规避阻断。
- 真实面板及本地协议放在忽略目录，不提交学生标识、成绩或证据附件。导入器不会从日志或姓名反推身份。

### 协议证据与审查

协议含处理对比、结果定义、共同起点、随访窗口、预设协变量、冻结负责人，以及分配、比较组、独立结果、基线变量、完整人群的证据说明。`assumptions` 分别记录平行趋势、无预期、无干扰的 `assumed/unverified` 状态与理由。

`assumed` 的含义是研究者明确接受该假设，不是程序验证成功。无 AI 日志不等于没有使用 AI；完整名册、污染和失访也需要来源证据，不能仅靠一个字符串得到保障。

主要阻断码：

|阻断码|含义|
|---|---|
|NO_VALID_CONTROL / NO_TREATED_STUDENTS|缺少一个比较臂|
|NO_INDEPENDENT_OUTCOME / OUTCOME_NOT_COMPARABLE|没有独立或可比结果的声明|
|MISSING_OUTCOME_NO_STRATEGY|前后测缺失；首版未实现缺失机制处理|
|BASELINE_NOT_PRETREATMENT / POST_TREATMENT_COVARIATE|基线或协变量的时序不合法|
|TIME_ZERO_UNVERIFIED / TREATMENT_START_MISMATCH|时间缺失或共同采用起点不成立|
|CONTROL_CONTAMINATION|对照记录出现处理开始时间|
|NO_COMMON_SUPPORT|某个基线分层缺少处理或对照|
|MIXED_TREATMENT_CLUSTER|不支持的簇内混合分配|
|TERM_MISMATCH / COVARIATE_SCHEMA_MISMATCH|学期或协变量合同不一致|
|PROTOCOL_NOT_FROZEN / DESIGN_EVIDENCE_MISSING / ASSUMPTION_UNDOCUMENTED|冻结或实质依据不完整|

审查输出分组人数、独立簇数、缺测计数、各分层人数及倾向得分。共同支持要求覆盖所有已纳入分层，首版不静默删掉控制组独有分层或修剪极端权重。协变量分层越细，越容易失去支持；不得看结果后合并类别以保留显著性。

### 平衡与权重诊断

审查中的 `diagnostics` 同时报告独立前测及各离散基线类别的加权前/后标准化差异。连续前测尺度取加权前两组样本方差均值的平方根；二元类别取两组 $p(1-p)$ 均值的平方根。加权后使用同一分母，避免改变尺度掩盖差异。两组均常量且相等时差异记 0；零方差但均值不同、样本不足或前测缺失时不伪造有限 SMD。

对照使用估计器相同的分层 odds 权重，ESS 为 $(\sum w)^2/\sum w^2$。另将权重按簇求和后计算簇级 ESS、最大簇占比；不会把同班学生当作独立班级。未满足共同支持时不生成加权诊断。

预设提示阈值为 $|SMD|>0.1$、分层倾向得分超出 $[0.05,0.95]$、最大单簇占比超过 20%。这些固定启发式阈值仅产生复核提示，不自动修剪、不证明无混淆，也不替代推断门槛。拟合变量被精确平衡是饱和分层的机械性质；未纳入的前测差异仍可能存在。协变量字段不一致时诊断为空并保留结构阻断。

## 估计方法

目标为在所记录假设下的采用者平均效应 ATT。令 $\Delta Y_i=Y_{i1}-Y_{i0}$，基线离散分类组成分层 $X_i$。每层拟合饱和模型：

$$\hat e(x)=\frac{n_{1x}}{n_{1x}+n_{0x}},\qquad
\hat m_0(x)=\frac{1}{n_{0x}}\sum_{i:D_i=0,X_i=x}\Delta Y_i.$$

令 $r_i=(1-D_i)\hat e(X_i)/(1-\hat e(X_i))$，使用

$$\hat\tau=\sum_i\left(\frac{D_i}{n_1}-\frac{r_i}{\sum_jr_j}\right)
\left(\Delta Y_i-\hat m_0(X_i)\right).$$

在这一饱和模型实现下，对照层内残差均值为零，公式等价于以处理组构成为权重的分层 DID。它是 DR-DID 的透明特例，**不是高维机器学习的双重稳健实现**，没有交叉拟合、连续倾向模型或自动特征选择。未调整 DID、合并前测样本标准差标准化的效应、对照权重 ESS 及最大权重一并保存。

因果解释仍依赖条件平行趋势、重叠、结果一致性、无预期和适当的干扰处理。方法依据及面板/重复横截面区别见 [Sant’Anna & Zhao (2020)](https://arxiv.org/html/1812.01723v3)。本实现不支持重复横截面、分批采用、跨学期混合、ITT/工具变量或处理后中介分析。

## 不确定性与异质性

在每个处理臂内部对完整簇有放回重抽样，每次重新计算两组饱和模型和效应。固定 seed 保持可复算。

- 95% CI 为有效重抽样估计的 2.5%/97.5% 分位数。
- 双侧近似 p 值为 $(1+\#\{|\hat\tau^*-\hat\tau|\ge|\hat\tau|\})/(B+1)$，检验零效应；这是中心化 bootstrap 近似，不是精确随机化检验，也不保证与百分位区间完全等价。
- 每臂簇数少于协议门槛时仅保留点估计。默认门槛 10，可提高，不能低于 10；这只是保守操作门槛，不是覆盖率保证。
- 任一重抽样缺少共同支持时，CI/p 值全部保持 null，状态 `bootstrap_support_failure`，并报告有效重复数。不丢掉失败样本后声称是无条件区间。
- 重抽样分布退化时状态 `degenerate_bootstrap`，不输出看似精确的零宽区间或 p 值。
- 各个预设基线层给出探索性点估计及可计算区间，不提供未经调整的子组显著性判断，也不把两组显著性不同解释为效应差异。
- 教育阈值比较基于区间下界，依赖预先确定的分数量纲与阈值；模板数值不是通用教学标准。

标准化效应首版仅给点值。区间不包含缺失数据、标注噪声、未测混淆和违背假设的全部不确定性。分层代码 `stratum_1` 等与审查产物的排序对应；报告不输出学生键、簇键或原始成绩。

## 固定偏差界

对于预设趋势偏差 $|b|\le B$，以估计的 DID 对比 $\hat\theta$ 构造 $[\hat\theta-B,\hat\theta+B]$；若基础有效 CI 为 $[L,U]$，另给 $[L-B,U+B]$。这些是固定假设下的敏感性结果。$B$ 由数据估计时还需处理其不确定性；这里不是完整 HonestDiD 实现，也不是已测出的趋势偏差。

## 验证证据

本地 Python 3.12 的完整测试共 106 项通过，B 模块 25 项覆盖已知效应、零效应、构成混淆、缺对照/缺测、错误时序、共同支持、重复身份、簇内复制、bootstrap 支持失败、假设失效反例、平衡与权重诊断、CLI 阻断、完整工作流及报告依赖。下列统计模拟来自此前记录的固定机制运行；新增诊断与流程不改变估计公式。

固定 seed 2045，三个场景各 100 次模拟，每次 200 次 bootstrap；每场景 100 次均可产生推断：

|场景|真实效应|额外非平行趋势|平均调整估计|未调整 DID 均值|真实效应覆盖率|零效应拒绝率|
|---|---:|---:|---:|---:|---:|---:|
|零效应、假设成立|0|0|-0.0463|1.9846|94%|5%|
|正效应、假设成立|5|0|4.9537|6.9846|94%|100%|
|零效应、假设失效|0|4|3.9537|5.9846|0%|100%|

前两行覆盖率的 Monte Carlo 标准误约为 2.37 个百分点，不能把 94% 当成精确总体覆盖率。最后一行故意展示识别失败：结构审查无法在只有两期数据时揭露虚假的平行趋势声明。0/100 或 100/100 也不代表总体概率严格为 0 或 1。

模拟为有限机制诊断，不是通用统计正确性的证明，也不代表 AI 在真实学生上有效。代码和配置变更后应记录新版本，不把旧模拟产物冒充新代码结果。

## 后续工作

真实使用前取得独立结果、比较组、采用时间、完整名册与课程依据；随后预先冻结协议。当前没有接入真实比赛因果数据，也没有进行真实效果检验。需要重复横截面、连续协变量、少簇专用推断、正式异质性差异检验或缺失机制处理时，另建明确设计和验证，不能通过放宽审查来冒充支持。
