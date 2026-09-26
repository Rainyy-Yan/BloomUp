"""Local aggregate-only reports; all values trace to immutable artifacts."""

from collections import defaultdict
import html
from pathlib import Path
import uuid

from .contracts import require


def build_report(store, metrics_id, observed_id=None, sensitivity_id=None, label_sensitivity_id=None):
    dependencies = [metrics_id] + [x for x in [observed_id,sensitivity_id,label_sensitivity_id] if x]
    for aid in dependencies: store.verify_tree(aid)
    metric = store.get(metrics_id,'metrics')['payload']
    analyses = []
    for aid,kind in [(observed_id,'observed'),(sensitivity_id,'sensitivity'),(label_sensitivity_id,'label_sensitivity')]:
        if aid:
            payload = store.get(aid,kind)['payload']
            require(payload['metrics']==metrics_id,'REPORT_VERSION_MISMATCH')
            analyses.append((kind,payload))
    title = '合成数据演示：不代表真实学生或比赛结果' if metric['synthetic'] else '内部探索性分析：非正式成绩'
    lines = [f'# {title}', '', '结论类别：描述性。未估计 AI 的因果增量效应，不生成学生排名。', '',
             f"数据版本：`{metric['dataset']}`", '', f'指标版本：`{metrics_id}`', '',
             f"范围状态：{metric['scope_status']}；参考分布状态：{metric['spec']['reference_status']}。", '',
             '分数描述已观察到的提问认知需求，不等同于学习能力、成绩或 AI 带来的提升。', '',
             '## 教师视图：学期汇总', '',
             '|学期|清单人数|有标签人数|有 AIV 人数|已准入轮次|有效标签轮次|平均 AIV|',
             '|---|---:|---:|---:|---:|---:|---:|']
    terms=defaultdict(list)
    for row in metric['rows']: terms[row['term']].append(row)
    fmt=lambda x:'缺失' if x is None else f'{x:.4f}'
    for term,rows in sorted(terms.items()):
        scores=[r['aiv'] for r in rows if r['aiv'] is not None]
        term=str(term).replace('|','/').replace('\n',' ')
        lines.append(f"|{term}|{len(rows)}|{sum(r['labeled_turns']>0 for r in rows)}|{len(scores)}|{sum(r['candidate_turns'] for r in rows)}|{sum(r['labeled_turns'] for r in rows)}|{fmt(sum(scores)/len(scores) if scores else None)}|")
    lines += ['', '## 学生视图：指标的解释边界', '',
              'HOT 是已标注提问中高阶需求的比例；CTQ 使用原始对话首末轮；DHI 是分布与指定课程参考分布的接近程度。', '',
              '本汇总不展示个人诊断。缺失 CTQ 时只给条件上下界；该范围固定已有 HOT/DHI，不覆盖全部缺失标签，也不是置信区间。', '',
              '## 管理视图：覆盖与来源', '', f"标签来源轮次数：`{metric['origins']}`", '',
              '所有指标均为探索性结果。标签缺失、解析准入与样本选择可能改变结果；学期间差异不能归因于 AI。']
    for kind,payload in analyses:
        if kind=='observed':
            lines += ['', '## 对话内观测变化', '', '学生等权汇总原始首末轮高阶需求指示值之差。区间为学期内相关组重抽样，不包含标注误差。', '']
            for row in payload['rows']:
                lines.append(f"- {row['term']}：{row['students']} 人，{row['components']} 组，观测均值 {fmt(row['mean_observed_hot_change'])}；95% 重抽样区间 {row['sampling_ci_95'] if row['sampling_ci_95'] else '组数不足，未估计'}。")
        elif kind=='sensitivity':
            lines += ['', '## 权重敏感性', '', '固定同一批可计算 AIV 的学生，仅改变权重；不解释为标注误差区间。', '', '|场景|人数|平均 AIV|','|---|---:|---:|']
            lines += [f"|{r['scenario']}|{r['students']}|{fmt(r['mean_aiv'])}|" for r in payload['rows']]
        else:
            lines += ['', '## 假设性标注扰动与重抽样', '',
                      '以下错误概率为预设情景，不是实测模型错误率或人工混淆矩阵。区间为模拟分位数，不是因果置信区间。', '',
                      '仅扰动区间固定当前学生；联合区间同时重抽学期内相关组。缺失标签始终保持缺失，学生排名不输出。', '',
                      '|学期|假设扰动概率|指标|有效学生|原观测均值|仅扰动95%范围|扰动与重抽样95%范围|',
                      '|---|---:|---|---:|---:|---|---|']
            bounds=lambda value:'未估计' if value is None else f'[{fmt(value[0])}, {fmt(value[1])}]'
            for row in payload['rows']:
                term=str(row['term']).replace('|','/').replace('\n',' ')
                for key,summary in row['metrics'].items():
                    lines.append(f"|{term}|{row['error_mass']:.0%}|{key.upper()}|{summary['students']}|{fmt(summary['observed'])}|{bounds(summary['perturbation_interval_95'])}|{bounds(summary['joint_resampling_interval_95'])}|")
    lines += ['', '## 复核定位', '', *[f'- `{aid}`' for aid in dependencies], '',
              '本文件仅本地生成。对外发布仍需按比赛权限检查聚合披露风险；本程序未上传任何数据。', '']
    markdown='\n'.join(lines)
    webpage='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>'+html.escape(title)+'</title><style>body{max-width:1100px;margin:40px auto;padding:0 24px;background:#f5f7fa;color:#17223b;font:16px/1.8 system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:28px;border-radius:12px}</style><body><pre>'+html.escape(markdown)+'</pre></body></html>'
    aid=store.put('report',{'metrics':metrics_id,'markdown':markdown,'html':webpage,'synthetic':metric['synthetic'],
                            'visibility':'internal_aggregate'},dependencies)
    directory=store.root/'workspaces'/uuid.uuid4().hex
    directory.mkdir(parents=True)
    (directory/'report.md').write_text(markdown,encoding='utf-8')
    (directory/'report.html').write_text(webpage,encoding='utf-8')
    return {'artifact_id':aid,'markdown':str(directory/'report.md'),'html':str(directory/'report.html')}
