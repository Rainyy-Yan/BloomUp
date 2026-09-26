# 受控预标注运行器

对应 #14。默认离线；`prediction-plan` 固定已有请求任务、模型及 revision、参数、提示词、rubric、schema、范围和请求哈希。`prediction-run` 无 `--execute` 只显示计划。真实网络调用还需 `--allow-network`，密钥只从配置指定的环境变量读取。MiniMax 仅允许已验证端点 `https://api.minimaxi.com/v1/responses`，不跟随重定向；不依赖 CC Switch。

## 命令链与验收边界

```text
prepare → dataset-import → dataset-admit（真实语义审查）
                     ↘ rubric-register → prediction-export → prediction-plan
                        → prediction-run → 既有 prediction-import 合同
                        → 独立人工 review / adjudicate → quality-evaluate
                        → labels-freeze → metrics-compute → analysis-observed / sensitivity
                        → report-build → verify
```

development 候选可在正式准入前用于规则调试，不称有效标注总体。audit/risk/all 请求仍要求冻结 rubric；冻结与人审独立性需要真实依据。预测不会变成人工金标签，也不会自动通过 quality 或 labels-freeze。`analysis-causal` 另需通过识别审查的独立数据；本运行器不解除该条件。

无需附件、密钥或网络的完整结构演示：

```text
python -m challenge --root local_state inference-demo
python -m unittest tests.test_inference -v
```

合成 provider 只允许 `synthetic=true` 的数据。演示将已知合成预测走实际运行器、导入、合成人审、质量评估、标签采用、AIV v2、观察分析和报告，保留一条故意错误。演示审阅者和质量参数均是假设夹具，不是真人人审或赛题阈值。B 的因果合同另用 `causal-demo` 验证。

真实使用先完成原有 `prediction-export`，复制 `configs/inference.minimax.template.json` 到忽略的 `configs/inference.local.json`。填入已批准总预算、历史保守费用和价格依据；空字段会阻止计划生成。所有相同预算的计划使用同一状态根目录和 `budget_id`。模型及参数来自导出任务，不能在运行时静默更换。

```text
python -m challenge --root local_state prediction-plan --task <prediction_task-id> --config configs/inference.local.json
python -m challenge --root local_state prediction-run --plan <inference_plan-id>
python -m challenge --root local_state prediction-run --plan <inference_plan-id> --execute --allow-network
```

只输出聚合回执及内部产物 ID；状态根目录内请求、模型响应、逐条标签与日志都是受控内部材料，不能提交 Git。使用 `local_state` 或仓库外受控目录；自定义目录须自行确认忽略规则。不要把密钥写入命令参数、配置或 Issue。

## 续跑、费用和失败

|状态|处理|
|---|---|
|尚未发出|预算预留满足后才调用|
|validated|核验原始缓存哈希和预测合同后复用；同一计划不重复发出|
|validation_failed|已取得且核验 usage，记录该次费用；默认停止，显式 `--retry-invalid` 最多达到配置的总尝试次数（上限 3），保留每次失败|
|submitted_unknown|发出前已经落盘预留；超时、中断、响应读取失败不等于免费，不自动重发|
|usage_unverified|缺少 usage、超出预留边界或返回模型名与计划不符，不能按配置价格核定费用，保留预留并暂停该预算后续调用|
|budget_exhausted|调用前停止，无新的请求|

预算账本在 `<root>/inference/ledger.json`，跨该 root 内同一 budget_id 的计划累计费用，不允许更换历史费用或上限来重置同名预算。互斥锁防止并发支出。保留锁时先核验进程状态；不能盲删。收到响应后按配置价格估算，有计费歧义则保留预留并停止。原始响应保留服务端 model、request id 和 usage；请求 revision 是声明值，浮动别名不能写成固定权重版本。

输入预留使用请求字节数加余量，输出使用 max_output_tokens；这只是工程保守估算，不是分词定理、真实发票或服务端硬限额。账本不覆盖其他目录、其他工具或账户并发消费。旧试点已经发生的费用应填入 prior_spend_cny，不能以新目录或新 budget_id 规避累计。未知计费须从服务方核对，当前不提供一键清账或自动重试。

原始缓存、计划任意变化均需独立版本。新建计划可能产生新请求；**要续跑就使用原计划 ID**，不能重建计划来假装复用。已有历史试点保留其原始提示词与导入产物，不假称新运行器生成，也不为了演示重发。

返回正文若包含本次凭据会在落盘前拒绝，错误回执只保留安全错误码。模型名称严格与计划匹配；提供方将浮动别名换成其他名字时需要重新核验，不静默按旧模型费用采用。该检查不等于服务方公开了模型权重快照。

## 当前证据

- implemented：Responses 适配、显式网络开关、预算账本、哈希缓存、断点恢复、受限格式重试、原导入合同。
- synthetic-verified：故障恢复测试和 `inference-demo` 的完整结构链路。
- historical real-inference-verified：旧 MiniMax 开发试点 12 个不同轮次、13 次调用，v1 的 8 条与 v1.1 的 4 条分别保存；不代表新适配器做过新增真实调用。
- independent-quality-verified：尚未达到，缺独立人审；真实 B/C 结果仍依赖相应输入和方法条件。

以上为最初候选的证据层级；后续新适配器联网验收见[工程验收记录](engineering-acceptance.md)。历史 9/12 NA 不是错误率；格式校验通过不等于认知标注正确。

## 可复跑的合成输入模型探针

`probe-prepare` 冻结四对公开合成输入，涵盖装饰性高阶词（两对）、输入内嵌等级指令、此前 AI 回答中的污染指令。准备和报告都不调用网络，实际调用仍走同一预算运行器：

```text
python -m challenge --root local_state probe-prepare --model configs/model.local.json --prompt prompts/认知预标注_v2_candidate.md
python -m challenge --root local_state prediction-plan --task <task-id> --config configs/inference.local.json
python -m challenge --root local_state prediction-run --plan <plan-id> --execute --allow-network
python -m challenge --root local_state probe-report --predictions <predictions-id>
```

模型配置使用既有 `{name, revision, parameters}` 合同。未启用网络时不会产生费用；重跑使用原计划。每对仅检验非缺失等级是否不变：不变记 PASS，改变记 FAIL，任一缺失记 NA。即便两次都错也可能不变，因此不能将其当准确率或人审金标准，更不能将四对探针写成所有红队攻击通过。v2 仍为开发候选，探针固定其哈希不等于完成独立人审后的规则冻结。
