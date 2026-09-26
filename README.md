# BloomUp：可追溯离线评价与假设性标签扰动

本仓库只准备存放逐文件审查过的脚本与方法说明。赛方原始工作簿、学生提问文本、脱敏学号、逐条人工标签和完整提交包**不在 Git 中共享**。私有仓库也不能替代赛题数据使用许可。

现已整合版本化数据、解析准入、双人审查、裁决、离线模型结果导入、质量门槛、冻结标签、指标与报告。原有研究入口继续保留。新增分析比较0%、10%、20%、30%的假设相邻标签错误质量，区分仅标签扰动与相关组重抽样后的范围；**它不是实测错误率或因果置信区间**。

## 无赛事数据也能运行

有现成依赖时直接运行；首次配置自己的环境可执行 `python -m pip install -r requirements.txt`。支持Python 3.11—3.13，CI覆盖Linux 3.11/3.13和Windows 3.13。

```text
python -m unittest discover -v
python -m challenge demo
python -m challenge status
```

Windows也可用 `./run_workbench.ps1 demo`，通过 `-PythonPath`指定已有解释器；Unix可用 `sh run_workbench.sh demo`。合成演示不读取原始附件、不调用模型，回执给出本地Markdown/HTML报告路径。当前是CLI及静态报告，没有Web服务。

完整步骤见 [操作指南](docs/workflow.md)，方法差异见 [整合说明](docs/integration.md)，人工规则见 [标注手册](docs/标注手册_v1.md)。

```mermaid
flowchart LR
    A[授权附件与候选解析] --> B[版本数据与解析准入]
    B --> C[双人审查与裁决]
    B --> D[离线预测导入]
    C --> E[原预测质量评估]
    D --> E
    C --> F[采用标签快照]
    E --> F
    F --> G[指标与描述分析]
    G --> H[权重及假设标签扰动]
    H --> I[可追溯本地报告]
```

## 当前可协作范围

- `tools/prepare_qa_text.py`：从授权的赛方工作簿构建本地 `data/qa_text.csv`。
- `tools/build_student_mapping.py`：在内存中核对记录与脱敏学生键；不写映射表。
- `tools/error_propagation.py`：基于 Mavis 的人工标签执行 T4.5.1 假设性相邻层扰动与学生聚类重抽样，只写学期级聚合。
- `tools/label_noise.py`：新旧入口共享的纯统计函数，不含数据或学生标识。
- `challenge/`：版本化轮次流水线，命令见 `python -m challenge --help`。
- `tests/`、`configs/`、`prompts/`和白名单方法文档：仅合成样本及通用模板。

本地的 `notes/error_propagation_report.md` 解释结果和限制；因它包含赛题衍生结果，**在数据使用许可确认前仍不进入 Git**。预设的 20% 扰动概率不是由单人标签估计出的真实混淆矩阵。

其余 `tools/`、`notes/`、`papers/`、`roadshow/`、`reproducibility/` 和 `data/` 默认忽略，不能用 `git add -f` 批量绕过。尤其不要提交 `tools/apply_annotations*.py`：这些脚本含人工逐条标签和记录标识。

## 本地运行前提

获许可的每名协作者自行把赛方同版脱敏附件放在 Git 仓库外；设置环境变量 `MATH_HACKATHON_SOURCE_DIR` 为两期附件目录的共同上级。Windows PowerShell 示例（将路径替换成自己的授权目录；以下为原有实验入口）：

```powershell
$env:MATH_HACKATHON_SOURCE_DIR = 'C:\path\to\authorized-attachments'
python tools/prepare_qa_text.py --source-root $env:MATH_HACKATHON_SOURCE_DIR
python tools/error_propagation.py
```

新轮次流程使用 `python -m challenge.pipeline prepare`，然后按操作指南固定数据与审查版本。新配置的source_root默认为空，无环境变量时在生成身份键前停止。日期及参考分布仍是待核实研究假设。需要自定义时保存为 `configs/preparation.local.json`并传 `--config`，不要覆盖固定实验配置。

旧入口的记录拼接单位、范围、固定数量合同与新轮次流程不同，不能将旧标签直接改成新turn_id，也不能把既有单人标签自动当成双人金标签或模型质量证据。

误差传播脚本还需要以下三个**包外取得或由 Mavis 经许可单独提供**的本地文件：

- `data/ground_truth_stratified_sample.csv`
- `data/ground_truth_stratified_sample_v2.csv`
- `data/ground_truth_turn_sample.csv`

没有同版人工标签和原始附件，就只能审查代码，不能复算论文数字。不要将这些文件、运行日志中的学生内容或任何 API key 加入 Git。脚本只生成 `data/error_propagation_results.csv` 两行聚合输出；该 CSV 当前仍留在本地，是否与朋友共享须先确认赛题许可。

## 两人协作约定

每个小任务从稳定分支建自己的分支，提交前明确列出文件、检查 `git diff --cached`，经另一人审查后再合并。不要让两人同时改同一份人工标签。提交代码时可用明确路径的 `git add -- <文件>`，不使用 `git add -A` 或自动全量上传。通过PR协作，不直接合并未经审查的研究变更。

`data/`、`runs/`、`.local/`、`local_state/`、本地配置、CSV/JSONL、密钥及研究报告均默认忽略。`.gitignore`不代替提交审查。新流程中的原预测与最终采用标签分别保存；缺失保持为空，不出精确名次；`analysis-causal`明确阻断。当前没有网络推理适配器，报告仅在本地生成，不自动发布。
