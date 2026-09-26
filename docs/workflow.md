# 离线工作流操作指南

命令从仓库根目录执行，python指向已有环境，依赖见requirements.txt。无附件也能执行 `python -m challenge demo`，输出仅代表合成演示。

## 1. 入口与准备

统一入口：`python -m challenge --help`。状态默认在local_state；全局--root放在子命令之前。demo每次在独立synthetic_demo子目录运行，回执给出报告路径。成功退出码0、契约阻断2、输入/IO失败1。

先设置仓库外授权附件路径MATH_HACKATHON_SOURCE_DIR。配置中source_root默认为空，未配置时在生成身份密钥前停止。日期、渠道、参考分布须核实，不可把模板假设说成已确认。

```text
python -m challenge.pipeline prepare
python -m challenge dataset-import --run runs/RUN_ID
python -m challenge rubric-register --file docs/标注手册_v1.md
python -m challenge parse-template --dataset DATASET_ID
```

RUN_ID从latest_run.json读取，大写ID均用上一步返回值替换。run须与生成清单的SHA-256一致。人工工作在导出的workspaces进行，固定run与原文件保持只读。不得删除.local身份键或baseline来绕过来源变化检查。

## 2. 解析准入

对照固定run的private/records.jsonl，实际核对后填写解析模板的reviewer_id与record_decisions：每条含record_id、status=accepted/rejected、reason。

```text
python -m challenge dataset-admit --dataset DATASET_ID --evidence 已填解析证据.json
```

默认仅准入逐条通过且原解析为format_ok_pending_review的记录，结构异常须先修正并生成新版本。scope_status默认provisional，confirmed必须有scope_evidence。若负责人选择抽检后推广，须完成全部固定解析样本、没有被拒绝的格式合格样本，并显式填写accept_remaining_format_ok=true和policy_reason；程序记录决策，不证明代表性。

## 3. 双人审查与裁决

```text
python -m challenge review-export --dataset DATASET_ID --rubric RUBRIC_ID --pool development --reviewer rater_A
python -m challenge review-export --dataset DATASET_ID --rubric RUBRIC_ID --pool development --reviewer rater_B
python -m challenge review-import --task TASK_A_ID --file 已填A.csv
python -m challenge review-import --task TASK_B_ID --file 已填B.csv
python -m challenge adjudicate --review-a REVIEW_A_ID --review-b REVIEW_B_ID --resolutions 裁决.json --actor adjudicator_C
```

不可修改前六列turn_id、dataset_id、rubric_id、text_hash、question、prior_context或增删样本。填写bloom_level=1—6或NA、当前提问的精确evidence_quote、outsourcing=yes/no/uncertain、insufficient_evidence=yes/no、confidence_1_to_5=1—5、任务分配的reviewer_id、ISO reviewed_at和notes。NA必须insufficient_evidence=yes并解释原因。CSV展示保护用的前导单引号不属于原文证据。

裁决文件为JSON数组，只包括实际等级或outsourcing冲突；无冲突为 `[]`。每条格式：

```json
[{"turn_id":"TURN_ID","label":4,"evidence":"实际原文片段","reason":"实际裁决依据","outsourcing":"no"}]
```

A/B及裁决者需不同ID，这不能证明真人独立性。开发后以 `rubric-register --file 最终手册.md --frozen-by 实际负责人` 冻结规则。audit必须用冻结规则独立评估，不能看金标签调提示词后仍称独立。草稿/冻结规则是不同版本，旧金标签不得手改ID沿用，须用新任务重新核对。

## 4. 模型结果与质量

```text
python -m challenge prediction-export --dataset DATASET_ID --rubric FROZEN_RUBRIC_ID --model configs/model.local.json --prompt prompts/认知预标注_v1.md --pool all
python -m challenge prediction-import --task PREDICTION_TASK_ID --file 实际响应.json
python -m challenge quality-evaluate --predictions PREDICTIONS_ID --gold AUDIT_GOLD_ID --policy configs/quality.local.json
```

model.local.json参照model.template.json填写实际名称、版本与参数。导出默认development；all/audit/risk要求冻结规则。请求含规则全文、提示词、question/prior_context，仍可能含隐私；导出不代表获准发送，程序不发起推理。

响应外层为 `{"task_id":"...","predictions":[...],"usage":null}`。每条预测遵守prompts/label.schema.json，所有请求ID恰好一次；证据属于当前提问。usage如已知可填input_tokens/output_tokens非负整数，未知保持null。

quality.local.json参照quality.template.json。真实数据门槛不低于配对数30、预测覆盖0.9、设计加权线性Kappa 0.6；audit_unseen、attested_by须由实际负责人按真实情况填写。这是操作门槛，不是官方标准或统计保障。宏F1平均固定六类，未支持类记0；保留混淆矩阵、NA计数。当前没有复杂抽样方差/Kappa置信下界。

质量始终评估原预测；人工修正只影响随后采用标签，不反写模型成绩。

## 5. 标签、指标与情景

```text
python -m challenge labels-freeze --admission ADMISSION_ID --rubric FROZEN_RUBRIC_ID --gold GOLD_ID
python -m challenge metrics-compute --labels LABELS_ID --spec configs/metrics.v2.json
python -m challenge analysis-observed --metrics METRICS_ID --replicates 1000
python -m challenge analysis-sensitivity --metrics METRICS_ID
python -m challenge analysis-label-sensitivity --metrics METRICS_ID --error-masses 0 0.1 0.2 0.3 --seed 2045 --replicates 1000
```

可只用人工金标签；采用模型时，labels-freeze同时指定--predictions和--quality，通过质量门槛才能采用。金标签优先，包括人工NA；未采用者保持缺失。多个不重叠金标签可重复--gold。

v2 默认 AIV=`100*(0.5*HOT+0.3*CTQ+0.15*DHI+0.05*MAB)`。HOT=L4—L6在有效标签中的比例，ABL=平均等级（辅助指标）；CTQ为原始多轮对话首末效用净差经`0.5+0.5*净差`映射后的均值；默认等距效用下等于`0.5+(末轮−首轮)/10`。对称DHI=`1−0.5*sum(abs(p−q))/(1−min(q))`；MAB=`log(1+实际使用Agent数)/log(1+冻结目录数)`。非对称DHI、凹合成及证明见[数学规范](aiv-mathematics.md)。

参考分布仍为暂定；真实使用前把模板复制到被忽略的 `configs/metrics.local.json`，填写课程依据、实际冻结目录与状态，并通过 `--spec` 指定。目录未知时MAB缺失；任何目录外Agent会阻断计算。不得以合成演示的目录代替真实可用机会。`metrics.v1.json`继续使用原三指标及未归一化DHI公式，不能直接混比分数。CLI `demo` 默认v2，`demo --formula-version legacy-v1`明确选择旧版。

原首末轮缺失则该对话CTQ缺失，不拿内部轮次补位；中间缺失保留覆盖。v2任何正权重指标缺失都不出AIV单值，给出固定已观察指标的条件上下界，不重新分配权重；不是置信区间或所有缺失标签的边界。学生清单中无准入轮次者仍保留缺失，不排名。

analysis-observed计算学生等权的首末高阶指示值变化，再按学期内相关组bootstrap，不是AI因果增量。analysis-sensitivity在v2下按学期、共同完整四指标样本比较三种预设方案、主方案及8个权重扰动，报告聚合Spearman相关和分差理论界；旧版仍比较8组权重。analysis-label-sensitivity将相邻等级扰动接入同一冻结标签与指标规范，见[方法说明](integration.md)。各情景保持相同缺失模式。

## 6. 报告与恢复

```text
python -m challenge report-build --metrics METRICS_ID --observed OBSERVED_ID --sensitivity WEIGHT_SENSITIVITY_ID --label-sensitivity LABEL_SENSITIVITY_ID
python -m challenge verify --artifact REPORT_ID
```

附加分析可省略；提供时必须对应同一metrics版本。报告仅聚合，不含原文/个人名次；对外披露仍需核对许可与小样本风险。HTML是静态报告，没有后台服务。

artifacts为权威不可变产物，workspaces为可编辑/导出文件。新导出使用新目录，不覆盖人审；同一内容复用ID，verify递归核对依赖。遇WRITER_BUSY先确认锁内PID已退出且无写者后人工清理，程序不破锁。进程硬中断可留下running回执，不能当完成。源码、配置或规则变化产生新版本。

analysis-causal返回CAUSAL_NOT_IDENTIFIED。当前没有因果估计器、网络模型适配器、自动旧标签迁移或真实比赛结果，合成演示不能替代这些工作。
