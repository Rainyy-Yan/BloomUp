# BloomUp 代码协作起点

本仓库只准备存放逐文件审查过的脚本与方法说明。赛方原始工作簿、学生提问文本、脱敏学号、逐条人工标签和完整提交包**不在 Git 中共享**。私有仓库也不能替代赛题数据使用许可。

## 当前可协作范围

- `tools/prepare_qa_text.py`：从授权的赛方工作簿构建本地 `data/qa_text.csv`。
- `tools/build_student_mapping.py`：在内存中核对记录与脱敏学生键；不写映射表。
- `tools/error_propagation.py`：基于 Mavis 的人工标签执行 T4.5.1 假设性相邻层扰动与学生聚类重抽样，只写学期级聚合。

本地的 `notes/error_propagation_report.md` 解释结果和限制；因它包含赛题衍生结果，**在数据使用许可确认前仍不进入 Git**。预设的 20% 扰动概率不是由单人标签估计出的真实混淆矩阵。

其余 `tools/`、`notes/`、`papers/`、`roadshow/`、`reproducibility/` 和 `data/` 默认忽略，不能用 `git add -f` 批量绕过。尤其不要提交 `tools/apply_annotations*.py`：这些脚本含人工逐条标签和记录标识。

## 本地运行前提

使用 Python 3.13，并运行 `python -m pip install -r requirements.txt`。获许可的每名协作者自行把赛方同版脱敏附件放在 Git 仓库外；设置环境变量 `MATH_HACKATHON_SOURCE_DIR` 为两期附件目录的共同上级。Windows PowerShell 示例（将路径替换成自己的授权目录）：

```powershell
$env:MATH_HACKATHON_SOURCE_DIR = 'C:\path\to\authorized-attachments'
python tools/prepare_qa_text.py --source-root $env:MATH_HACKATHON_SOURCE_DIR
python tools/error_propagation.py
```

误差传播脚本还需要以下三个**包外取得或由 Mavis 经许可单独提供**的本地文件：

- `data/ground_truth_stratified_sample.csv`
- `data/ground_truth_stratified_sample_v2.csv`
- `data/ground_truth_turn_sample.csv`

没有同版人工标签和原始附件，就只能审查代码，不能复算论文数字。不要将这些文件、运行日志中的学生内容或任何 API key 加入 Git。脚本只生成 `data/error_propagation_results.csv` 两行聚合输出；该 CSV 当前仍留在本地，是否与朋友共享须先确认赛题许可。

## 两人协作约定

每个小任务从稳定分支建自己的分支，提交前明确列出文件、检查 `git diff --cached`，经另一人审查后再合并。不要让两人同时改同一份人工标签。提交代码时可用明确路径的 `git add -- <文件>`，不使用 `git add -A` 或自动全量上传。当前仓库尚无 GitHub 远端；配置远端、邀请协作者和首次推送均需由仓库所有者决定。
