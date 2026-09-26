# 整合设计与研究边界

BloomUp原有脚本擅长基于既有人工标签开展聚合扰动实验；新增离线流水线负责把数据、审查、预测、采用标签、指标和报告绑定到可核对版本。两部分共用相邻扰动核与分位数函数，保留原有实验入口。

## 保留的旧合同

prepare_qa_text.py仍按原规则构造记录级question_text、qid及固定数量；build_student_mapping.py仍核对源行并仅在内存中维护学生映射。原统计脚本的入口、SEED、REPS、20%情景和输出列不变。

共享函数抽出后增加参数检查。正常输入的数值兼容性使用固定随机种子的合成四学生样本对照原算法验证；没有在本次整合中运行真实人工标签或复算论文结果。包内导入和直接脚本调用均可用。

评分入口收敛到`challenge.metrics.calculate_students()`，依据明确版本调用legacy-v1或aiv-v2。两者共用基础观察统计和原始首末轮处理，各自只计算一次版本公式。已移除仅被测试调用的`challenge.core.metrics()`和`challenge.core.asymmetric_dhi()`：旧版学生评分仍可调用`student_metrics()`；归一化DHI使用`scoring.normalized_dhi()`。这些接口输入和返回值不同，仓外直接导入旧core函数的代码需要迁移，不能机械替换函数名。

## PR #3 与 PR #8 的验收对照

核对基线 main `d28419c`（已含 #2/#4），候选 #8 `80b5f74`，旧 Draft #3 `89e4419`。以下是作者侧差异核对，不代替维护者审查或合并决定。

|旧修复意图|#8 保留/调整的行为|对应证据|
|---|---|---|
|质量模板空置与取消固定底线|保留 null 准则；未确认不采纳模型标签；Kappa 不可计算仍阻断|test_unconfirmed_quality_reports_metrics_but_blocks_adoption、test_explicit_quality_policy_has_no_hardcoded_real_data_floor|
|旧版敏感性按学期|保留分学期，并输出空学期的缺失均值；不改 v2 的方案比较|test_legacy_sensitivity_separates_terms_including_empty_scores、ScoringWorkflow|
|不导出学生键 CSV|保留；另经 readiness 哈希保留来源总人数，报告区分两个分母|test_window_roster_channel_and_no_invented_scores、test_import_preserves_population_counts_without_roster_only_identities|
|reporting.py 集成|在包含 v2 报告分支的基线上修改旧版显示，不用 #3 的旧文件覆盖 v2 内容|test_v2_demo_report_noise_and_dependency_tree；历史无 term 产物显示旧版未分学期|
|workflow.md 集成|替换旧质量阈值、人数与学期说明；保留 v2 数学边界及 #4 的 B 模块入口|工作流文档逐段对照；B 阻断/演示回归仍在完整套件|

候选共16个变更文件：challenge 的 CLI、analysis、core、evaluation、ingest、metrics、pipeline、reporting；quality.template；integration/workflow/标注手册；test_core/test_pipeline/test_workflow；tools/error_propagation。#8 不机械合并 #3，因此没有把冲突两侧整文件二选一；以上表格列出保留和调整的语义。

验证记录：`python -m unittest discover -s tests -q` 退出0，112项通过；400次合成旧/新评分比较通过（1e-12容差，最大绝对差1.42e-14）。[候选 CI](https://github.com/Rainyy-Yan/BloomUp/actions/runs/36225082265)三个环境通过。`demo`、`causal-demo` 使用短状态目录退出0；同一代码哈希 `8988312a525f58406b6ba679a4db9ef4b76f7edde75c18709300f2a1c963ea6e` 下的 `status` 在2026-09-26退出0。深路径 demo 曾触发 Windows WinError 3，原存储路径限制未修复。

#8 尚未合并；#3 尚未关闭。维护者确认替代关系及合并后，再核验实际 main，不将上述候选检查冒充合并验收。

## 不直接合并的研究对象

| 维度 | 原有脚本 | 新流水线 |
|---|---|---|
| 主要单位 | 记录拼接文本，另有独立轮次表 | 保留对话结构的单个提问轮次 |
| 范围 | 原版名单与固定数量合同 | 显式名单、渠道、暂定日期及排除原因 |
| 解析 | 既有Q/A标记提取 | 结构异常隔离；格式合格仍须语义审查 |
| 人工证据 | 既有单人标签文件 | 版本绑定的独立A/B判断及第三人裁决 |
| 扰动核 | 每次重抽样的标签频率分配20%假设质量 | 每学期固定观察频率分配指定假设质量 |
| 重抽样 | 分开的记录/轮次学生集 | 同一冻结轮次快照，按学生及精确重复关联组整体抽样 |
| 区间 | 保留历史ci字段与解释 | 区分仅扰动分位数和联合重抽样分位数 |
| 排序 | 保留历史top-quartile与flip字段 | 默认聚合；显式 student-export 可在受控本地按学期完整人群导出排名 |

不能直接拿两个入口的数值解释为同一总体的差异，也不能将旧qid映射成turn_id后复用标签。实际迁移须固定源文件与范围、核对解析正文、由真实审阅者确认；本次不提供绕过确认的自动标签迁移。

## 标注扰动的定义

对已观察等级l，保留概率为1−p，剩余p只分配给相邻等级。相邻权重为该学期已采用标签中的频数加0.5；端点L1/L6只有一个邻居。p是用户指定的假设质量，频率不是“预测等级→真实等级”的转移观察。

每个复制轮次先扰动每条已知标签一次；NA不参与且始终NA。随后以准备阶段的相关组为单位在学期内有放回抽样。同一原学生在一个复制中被重复抽到时使用同一次扰动结果。各p使用共同随机数，降低情景间不必要的Monte Carlo噪声。

HOT保持0—1尺度，与旧脚本HOT_pct不同。每学期的指标均值按有效学生等权；不同指标的可计算学生数分别展示。CTQ永远基于原始对话首末轮，缺失端点不从内部轮次补位。AIV只在CTQ可计算时给单值。

- 仅扰动95%范围：固定当前观察学生，只改变已知标签，取模拟结果2.5%和97.5%分位数。
- 联合95%范围：同时引入相关组重抽样；某指标少于5个支持组，或某次重抽没有可估计值，则不报告其联合范围，保留缺失原因与有效复制数。
- 范围不覆盖未知学生、未准入记录及未标注提问的选择偏差，不识别因果效果，不代表校准后的置信区间。原始点估计、缺失CTQ上下界和这些情景范围保持分开。

## 完整性与权限

产物ID包含内容、输入版本、配置哈希和实现哈希；实现哈希覆盖challenge与共享tools源码。产物使用短临时名加原子替换，避免Windows深目录下临时文件名过长。校验依赖树可发现修改，但不是数字签名或访问控制。

模型请求默认仅导出内部文件；prediction-run 显式启用后通过预算约束调用 MiniMax，详见 inference.md。所有真实正文、标注及派生结果都在忽略目录，只有人工审查后的源码白名单允许暂存。新CLI不连接旧论文结果，不生成看似完成的人审、模型准确率或真实成绩。

## 验证与后续

检查覆盖版本错绑、原文变更、重复行、双人冲突、缺失端点、预测证据、质量门槛、原预测与采用标签分离、错误质量边界、相关组支持数、合成整链路和Git隐私忽略规则。CI不依赖赛方附件。

仍需实际工作：独立人审及解析准入、真实预测质量验证（MiniMax 许可、预算及适配器联网验收已有）、如有可识别证据再讨论经验误差模型、因果设计及正式比赛材料。流程记录证据，不代替取得证据。
