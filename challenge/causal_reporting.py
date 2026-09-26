"""Local aggregate reports for causal audits, estimates and simulation validation."""

import html
import uuid

from .contracts import require


def build_causal_report(store, analysis_id):
    store.verify_tree(analysis_id)
    artifact = store.get(analysis_id)
    require(artifact['artifact_type'] in ('causal_audit','causal_effect','causal_validation'),'INVALID_CAUSAL_REPORT_INPUT')
    result = artifact['payload']
    if artifact['artifact_type'] == 'causal_validation':
        return _validation_report(store,analysis_id,result)
    audit = result if artifact['artifact_type'] == 'causal_audit' else store.get(result['audit'],'causal_audit')['payload']
    protocol = audit['protocol']
    title = 'B 模块合成验证：不代表真实学生效果' if audit['synthetic'] else 'B 模块内部研究：条件性因果分析'
    lines = [f'# {title}', '', f"审查状态：{audit['status']}", '',
             f"学期：{protocol['term']}；处理对比：{protocol['treatment_contrast']}。", '',
             f"结果变量：{protocol['outcome']['name']}；量尺范围：[{protocol['outcome']['lower']}, {protocol['outcome']['upper']}]。", '',
             f"研究者声明：独立测量={protocol['outcome']['independent']}，可比={protocol['outcome']['comparable']}，高分更好={protocol['outcome']['higher_is_better']}。", '',
             f"共同起点：{protocol['time_zero']}；随访窗口：{protocol['followup_window']}。", '',
             '审查仅核对数据结构与研究者声明，不证明平行趋势、无干扰或无自选择偏差。', '',
             f"处理/对照人数：{audit['students']}；独立簇数量：{audit['clusters']}。", '',
             f"阻断原因：{', '.join(audit['blockers']) or '无结构阻断；解释依赖已记录假设'}。", '',
             '仅支持同一学期、共同处理起点、完整前后测与预设离散基线分层；不将日志 AIV 当作独立学习终点。']
    diagnostics = audit.get('diagnostics')
    if diagnostics is not None:
        lines += ['', '## 基线平衡、重叠与权重', '',
                  'SMD 使用加权前的固定尺度；空值表示未定义或不可计算。阈值仅提示复核，不证明识别成立。', '',
                  f"诊断提示：{', '.join(diagnostics['warnings']) or '未触发预设提示，仍需实质假设论证'}。", '',
                  f"分层倾向范围：[{diagnostics['overlap']['propensity_min']:.4f}, {diagnostics['overlap']['propensity_max']:.4f}]；缺乏支持的人数：{diagnostics['overlap']['unsupported_students']}。", '',
                  '|基线项目|处理可用人数|对照可用人数|加权前 SMD|加权后 SMD|尺度状态|',
                  '|---|---:|---:|---:|---:|---|']
        fmt = lambda v: '未定义' if v is None else f'{v:.4f}'
        for r in diagnostics['balance']:
            feature = str(r['feature']).replace('|','/').replace('\n',' ')
            category = '' if r['category'] is None else '='+str(r['category']).replace('|','/').replace('\n',' ')
            lines.append(f"|{feature}{category}|{r['treated_available']}|{r['control_available']}|{fmt(r['smd_before'])}|{fmt(r['smd_after'])}|{r['scale_status']}|")
        weights = diagnostics['weights']
        if weights is None:
            lines += ['', '共同支持不足，不生成加权诊断，不裁剪倾向得分或删除样本。']
        else:
            lines += ['', f"对照学生权重 ESS：{weights['control_ess']:.2f}；对照簇权重 ESS：{weights['control_cluster_ess']:.2f}；处理簇权重 ESS：{weights['treated_cluster_ess']:.2f}。", '',
                      f"最大单簇权重占比：对照 {weights['max_control_cluster_share']:.2%}，处理 {weights['max_treated_cluster_share']:.2%}。", '',
                      '分层变量的精确平衡是拟合产生的性质，不表示所有基础差异或未测混淆已经消失。']
    if artifact['artifact_type'] == 'causal_effect':
        lines += ['', '## 效应估计', '', f"调整后 ATT：{result['estimate']:.6f}；未调整 DID：{result['unadjusted_did']:.6f}。", '',
                  f"95% 区间：{result['ci95']}；双侧近似 p 值：{result['p_value']}；推断状态：{result['inference_status']}。", '',
                  f"标准化效应（合并前测样本标准差）：{result['standardized_effect']}；对照权重 ESS：{result['control_ess']:.2f}。", '',
                  f"预设教育意义阈值：{result['educational_threshold']}；区间下界超过阈值：{result['educational_threshold_exceeded']}。", '',
                  '区间与检验采用组内整簇重抽样并重新拟合；簇不足、退化或任一重抽样丢失支持时不输出推断。', '',
                  '## 探索性分层', '', '分层仅使用冻结的处理前变量。区间未经多重比较调整，不用于宣称组间效应差异。', '',
                  '|分层|处理前条件|人数|效应|95% 区间|推断状态|', '|---|---|---:|---:|---|---|']
        definitions = {r['stratum']:r['covariates'] for r in audit['strata']}
        for r in result['heterogeneity']:
            conditions = ', '.join(f'{k}={v}' for k,v in definitions.get(r['stratum'],{}).items()) or '无额外分层'
            conditions = conditions.replace('|','/').replace('\n',' ')
            lines.append(f"|{r['stratum']}|{conditions}|{r['students']}|{r['estimate']:.6f}|{r['ci95']}|{r['inference_status']}|")
        lines += ['', '## 固定趋势偏差界', '', '|假设偏差上界|效应条件界|扩展置信范围|','|---:|---|---|']
        for r in result['sensitivity']:
            lines.append(f"|{r['bias_bound']}|{r['effect_bounds']}|{r['conservative_ci95']}|")
        lines += ['', '这些偏差界是预设假设，不是实测误差；不是完整的 HonestDiD 实现。']
    lines += ['', '## 复核', '', f'输入产物：`{analysis_id}`', '',
              '报告只含汇总。真实数据的授权、对照定义、独立评分与假设依据须由研究负责人核实；本程序不上传数据。', '']
    return _save_report(store,analysis_id,title,lines,audit['synthetic'])


def _validation_report(store, analysis_id, result):
    require(result.get('synthetic') is True,'VALIDATION_MUST_BE_SYNTHETIC')
    title = 'B 模块合成模拟验证：不代表真实教学效应'
    lines = [f'# {title}', '', f"seed={result['seed']}；每场景模拟 {result['simulations']} 次；每次重抽样 {result['replicates']} 次。", '',
             '该报告直接读取指定验证产物，不重新估计或拼接其他版本结果。', '',
             '|场景|真实效应|非平行趋势|平均估计|估计偏差|可推断次数|覆盖率|零效应拒绝率|',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    percent = lambda v: '未估计' if v is None else f'{v:.1%}'
    for r in result['rows']:
        lines.append(f"|{r['scenario']}|{r['true_effect']}|{r['imposed_differential_trend']}|{r['mean_estimate']:.4f}|{r['bias']:.4f}|{r['inference_available']}/{r['simulations']}|{percent(r['coverage_among_valid'])}|{percent(r['rejection_rate_among_valid'])}|")
    lines += ['', '覆盖率和拒绝率以可推断重复为分母。有限模拟不证明一般覆盖率；0%/100%不等于总体概率严格为0/1。', '',
              '违反平行趋势的反例用于展示识别失败；程序检查不能证明研究者的反事实假设。', '',
              '## 复核', '', f'验证产物：`{analysis_id}`', '']
    return _save_report(store,analysis_id,title,lines,True)


def _save_report(store, analysis_id, title, lines, synthetic):
    markdown = '\n'.join(lines)
    webpage = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>'+html.escape(title)+'</title><body><pre style="white-space:pre-wrap">'+html.escape(markdown)+'</pre></body></html>'
    aid = store.put('causal_report',dict(analysis=analysis_id,markdown=markdown,html=webpage,synthetic=synthetic),[analysis_id])
    directory = store.root/'workspaces'/uuid.uuid4().hex
    directory.mkdir(parents=True)
    (directory/'report.md').write_text(markdown,encoding='utf-8')
    (directory/'report.html').write_text(webpage,encoding='utf-8')
    return dict(artifact_id=aid,markdown=str(directory/'report.md'),html=str(directory/'report.html'))
