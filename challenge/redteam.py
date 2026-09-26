"""Eight explicitly scoped synthetic attacks; semantic model claims may be NA."""

from collections import Counter
import io
import unittest

from .core import make_splits, parse_dialogue
from .metrics import student_metrics
from .scoring import breadth_score, transition_quality


def run_attacks():
    rows=[]
    def add(number,name,source,executed,n,change,status,explanation):
        rows.append(dict(attack_id=f'R{number:02}',name=name,input_source=source,executed=executed,
                         n_attacked=n,observed_change=change,pass_fail_na=status,explanation=explanation,
                         command='python -m challenge red-team'))

    def score(levels):
        turns=[dict(turn_id=str(i),record_id='synthetic-record',student_key='synthetic-student',
                    term='synthetic',turn_index=i,agent_id=None) for i in range(1,len(levels)+1)]
        return student_metrics(turns,{str(i):v for i,v in enumerate(levels,1)},[.1,.2,.25,.25,.15,.05],[.5,.3,.2])[0]

    first=parse_dialogue('Q: 请说明均值。\nA: 简单回答。')
    changed=parse_dialogue('Q: 请说明均值。\nA: 创新、批判和深度分析的合成答案。')
    unchanged=first['turns']==changed['turns']
    add(1,'替换当前 AI 答案','synthetic_parser_fixture',True,1,dict(candidate_input_changed=not unchanged),
        'PASS' if unchanged else 'FAIL','只验证当前 AI 答案没有进入当前学生输入；未验证真实模型判级不受此前上下文污染。')

    add(2,'低阶请求追加高阶词','synthetic_attack_design',False,0,None,'NA',
        '已定义词汇诱导攻击，但没有在冻结模型及独立参照下运行；提示词声明不能冒充模型鲁棒性证据。')

    low,outsourced,genuine=score([1,1]),score([6,6]),score([6,6])
    add(3,'复制高级请求或外包','synthetic_fixed_label_counterexample',True,1,
        dict(hot_before=low['hot'],hot_after=outsourced['hot'],same_as_genuine=genuine['hot']==outsourced['hot']),'FAIL',
        '条件反例：同为 L6 的真实设计请求与复制/外包请求给出相同 HOT。所请求任务层次不能识别掌握程度；未声称模型识别复制的失败率。')

    base=[1,6];attack=base+[6]*9
    before=score(base)['hot'];after=score(attack)['hot']
    add(4,'重复高阶请求灌水','synthetic_fixed_label_counterexample',True,9,
        dict(hot_before=before,hot_after=after,delta=after-before),'FAIL' if after>before else 'PASS',
        '固定等级后重复高阶请求会改变轮次加权 HOT。现有等级比例不是去重后的学习增益；不更改公式掩盖失败。')

    utilities=[0,.2,.4,.6,.8,1]
    original=transition_quality([2,5],utilities);cycle=transition_quality([2,6,1,6,1,5],utilities)
    add(5,'插入循环中间轮次','synthetic_endpoint_fixture',True,4,
        dict(ctq_before=original,ctq_after=cycle,delta=cycle-original),'PASS' if original==cycle else 'FAIL',
        '仅原始端点 CTQ 不变；该性质不表示整段持续改善，也不保证 HOT/DHI 不受新增轮次影响。')

    before=breadth_score(1,3);after=breadth_score(3,3)
    add(6,'刷无实质差异的目录内工具','synthetic_catalog_counterexample',True,2,
        dict(mab_before=before,mab_after=after,delta=after-before),'FAIL' if after>before else 'PASS',
        '工具次数不重复计数，但增加不同目录内身份仍能提高 MAB；缺少实质使用/机会校准时不能解释成广度收益。春季真实工具身份仍未知。')

    suite=unittest.defaultTestLoader.loadTestsFromName(
        'tests.test_pipeline.PreparationTests.test_window_roster_channel_and_no_invented_scores')
    outcome=unittest.TextTestRunner(stream=io.StringIO(),verbosity=0).run(suite)
    passed=outcome.testsRun==1 and outcome.wasSuccessful()
    add(7,'混入名单外、其他日期与渠道','synthetic_workbook_regression',True,3,
        dict(regressions_run=outcome.testsRun,failures=len(outcome.failures),errors=len(outcome.errors)),
        'PASS' if passed else 'FAIL','实际执行既有合成工作簿排除回归；不证明当前真实教学日期已确认。')

    long_text='用于合成泄漏测试的相同长文本包含充分的相同字词以超过精确重复关联阈值不会使用真实学生内容'
    turns=[dict(turn_id=f't-{i}-{j}',student_key=f's-{i}',term='synthetic',
                question=long_text if i<2 else f'合成独立内容{i}-{j}') for i in range(80) for j in range(2)]
    split=make_splits(turns)
    overlap=len([tid for ids in split['selected'].values() for tid in ids])-len({tid for ids in split['selected'].values() for tid in ids})
    same=split['pool_by_student']['s-0']==split['pool_by_student']['s-1']
    add(8,'同人、重复内容与标签提示泄漏','synthetic_split_fixture',True,len(turns),
        dict(exact_duplicate_group_kept_together=same,selected_overlap=overlap),
        'BOUNDARY' if same and overlap==0 else 'FAIL',
        '同人/长精确重复的分池约束已执行；语义近重复和输入内嵌标签的真实模型攻击尚未验证，不能写全类通过。')
    return dict(synthetic=True,rows=rows,status_counts=dict(Counter(r['pass_fail_na'] for r in rows)),
                real_model_robustness_verified=False,historical_attack_results_reproduced=False,
                interpretation='Engineering checks and fixed-label metric counterexamples; not measured student or model error rates.')
