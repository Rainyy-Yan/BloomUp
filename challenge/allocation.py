"""Small exact audit-allocation planner. Forecasts never certify model quality.

One term per configuration; all frame units receive one AI attempt. Optimize
integer double-review counts, with explicit adjudication and finite-frame bounds.
"""

from decimal import Decimal, InvalidOperation
from copy import deepcopy
import itertools
import math

from .contracts import finite_number, require


def _cost(value):
    require(type(value) in (int, float, str), 'INVALID_ALLOCATION_COST')
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('INVALID_ALLOCATION_COST') from None
    require(result.is_finite() and result >= 0, 'INVALID_ALLOCATION_COST')
    return result


def _radius(frame_size, n, alpha_each):
    if n == frame_size:
        return 0.0
    if n == 0:
        return 1.0
    return min(1.0, math.sqrt((frame_size-n)/(4*n*(frame_size-1)*alpha_each)))


def _dominates(left, right):
    return (_cost(left['cost_cny']) <= _cost(right['cost_cny']) and
            left['projected_lower_six'] >= right['projected_lower_six'] and
            left['projected_lower_three'] >= right['projected_lower_three'])


def solve_allocation(spec):
    require(isinstance(spec, dict) and set(spec) == {'term','evidence_status','alpha','targets','budget_cny',
                                                   'max_combinations','strata','parameter_sources'}, 'INVALID_ALLOCATION_SPEC')
    require(isinstance(spec['term'],str) and spec['term'].strip(), 'ONE_TERM_REQUIRED')
    require(spec['evidence_status'] in ('synthetic_scenario','real_pilot_estimate'), 'INVALID_EVIDENCE_STATUS')
    require(isinstance(spec['parameter_sources'],dict) and bool(spec['parameter_sources']), 'PARAMETER_SOURCES_REQUIRED')
    alpha=finite_number(spec['alpha'],0,1)
    require(0 < alpha < 1, 'INVALID_ALPHA')
    targets=spec['targets']
    if targets is not None:
        require(isinstance(targets,dict) and set(targets)=={'six','three'}, 'INVALID_QUALITY_TARGET')
        for value in targets.values(): finite_number(value,0,1)
    budget=None if spec['budget_cny'] is None else _cost(spec['budget_cny'])
    limit=spec['max_combinations']
    require(type(limit) is int and 1 <= limit <= 200000, 'INVALID_SEARCH_LIMIT')
    strata=spec['strata']
    require(isinstance(strata,list) and 0 < len(strata) <= 12, 'INVALID_STRATA')
    result=dict(status='blocked', term=spec['term'], evidence_status=spec['evidence_status'],
                formal_recommendation=False, independent_audit_verified=False,
                optimal=None, alternatives=[], frontier=[], blocked_reasons=[], pilot_summary=[],
                method='exact_integer_enumeration_finite_frame_chebyshev_v1',
                quality_metric='exact_agreement_not_kappa',
                assumptions=['One AI attempt per frame unit; failure/NA counts as nonagreement.',
                             'Future audits are simple random samples without replacement within declared strata.',
                             'Projected audit means equal development pilot proportions; transportability is an assumption.',
                             'Two independent human reviews per audited unit; adjudication rate is a planning estimate.',
                             'Forecast bounds are not observed audit confidence intervals or a quality pass.'])
    names=set()
    alpha_each=alpha/(2*len(strata))
    required={'name','frame_size','min_reviews','pilot_confusion','pilot_prediction_na',
              'ai_unit_cny','double_review_unit_cny','adjudication_unit_cny','disagreement_rate',
              'quality_floor','min_prediction_coverage'}
    costs=[]
    for h in strata:
        require(isinstance(h,dict) and set(h)==required,'INVALID_STRATUM')
        require(isinstance(h['name'],str) and h['name'].strip() and h['name'] not in names, 'INVALID_STRATUM_NAME')
        names.add(h['name'])
        size,minimum=h['frame_size'],h['min_reviews']
        require(type(size) is int and 1 <= size <= 10000, 'INVALID_FRAME_SIZE')
        require(type(minimum) is int and 0 <= minimum <= size, 'INVALID_REVIEW_MINIMUM')
        matrix=h['pilot_confusion']
        require(isinstance(matrix,list) and len(matrix)==6 and all(isinstance(r,list) and len(r)==6 for r in matrix), 'INVALID_CONFUSION')
        require(all(type(v) is int and v >= 0 for r in matrix for v in r), 'INVALID_CONFUSION_COUNT')
        na=h['pilot_prediction_na']
        require(type(na) is int and na >= 0,'INVALID_NA_COUNT')
        labeled=sum(map(sum,matrix));total=labeled+na
        six=sum(matrix[i][i] for i in range(6))/total if total else None
        three=sum(matrix[i][j] for i in range(6) for j in range(6) if i//2==j//2)/total if total else None
        unsupported=[i+1 for i in range(6) if sum(matrix[i])==0]
        # This pilot bound requires independent representative pairs; it is a diagnostic under that assumption.
        pilot_radius=min(1., math.sqrt(1/(4*total*alpha_each))) if total else None
        result['pilot_summary'].append(dict(name=h['name'], paired_nonmissing=labeled, prediction_na=na,
                                           attempted_with_valid_gold=total,
                                           six_agreement=six, three_agreement=three,
                                           prediction_coverage=labeled/total if total else None,
                                           unsupported_gold_classes=unsupported,
                                           pilot_interval_assuming_independent_pairs={
                                               k:[max(0,p-pilot_radius),min(1,p+pilot_radius)] if p is not None else None
                                               for k,p in [('six',six),('three',three)]}))
        if not total: result['blocked_reasons'].append('no_paired_pilot')
        if any(h[k] is None for k in ('ai_unit_cny','double_review_unit_cny','adjudication_unit_cny')):
            result['blocked_reasons'].append('unknown_cost');costs.append(None)
        else: costs.append([_cost(h[k]) for k in ('ai_unit_cny','double_review_unit_cny','adjudication_unit_cny')])
        if h['disagreement_rate'] is None: result['blocked_reasons'].append('unknown_adjudication_rate')
        else: finite_number(h['disagreement_rate'],0,1)
        require(isinstance(h['quality_floor'],dict) and set(h['quality_floor'])=={'six','three'}, 'STRATUM_QUALITY_FLOOR_REQUIRED')
        for value in h['quality_floor'].values(): finite_number(value,0,1)
        finite_number(h['min_prediction_coverage'],0,1)
    if result['blocked_reasons']:
        result['blocked_reasons']=sorted(set(result['blocked_reasons']))
        return result
    combinations=math.prod(h['frame_size']-h['min_reviews']+1 for h in strata)
    result['combinations']=combinations
    if combinations > limit:
        result.update(status='search_limit',blocked_reasons=['enumeration_limit_not_infeasibility'])
        return result
    population=sum(h['frame_size'] for h in strata)
    choices=[]
    for h,pilot,cost in zip(strata,result['pilot_summary'],costs):
        options=[]
        for n in range(h['min_reviews'],h['frame_size']+1):
            radius=_radius(h['frame_size'],n,alpha_each)
            a=math.ceil(Decimal(n)*Decimal(str(h['disagreement_rate'])))
            total=h['frame_size']*cost[0]+n*cost[1]+a*cost[2]
            options.append(dict(name=h['name'],frame_size=h['frame_size'],m_ai=h['frame_size'],n_double_review=n,
                                a_adjudicate=a,precision_radius=radius,cost_cny=str(total),
                                lower_six=max(0.,pilot['six_agreement']-radius),
                                lower_three=max(0.,pilot['three_agreement']-radius),
                                predicted_nonmissing_coverage=pilot['prediction_coverage']))
        choices.append(options)
    feasible=[];frontier=[];minimum_quality_cost=None
    for allocation in itertools.product(*choices):
        cost=sum((_cost(r['cost_cny']) for r in allocation),Decimal(0))
        six=sum(r['frame_size']/population*r['lower_six'] for r in allocation)
        three=sum(r['frame_size']/population*r['lower_three'] for r in allocation)
        candidate=dict(cost_cny=str(cost),projected_lower_six=six,projected_lower_three=three,strata=list(allocation))
        stratum_constraints=all(r['lower_six'] >= h['quality_floor']['six'] and
                                r['lower_three'] >= h['quality_floor']['three'] and
                                r['predicted_nonmissing_coverage'] >= h['min_prediction_coverage']
                                for r,h in zip(allocation,strata))
        good=targets is not None and six >= targets['six'] and three >= targets['three'] and stratum_constraints
        if good:
            minimum_quality_cost=cost if minimum_quality_cost is None else min(minimum_quality_cost,cost)
            if budget is None or cost <= budget:
                feasible.append(candidate)
                feasible.sort(key=lambda x:(_cost(x['cost_cny']),-x['projected_lower_six'],-x['projected_lower_three']))
                del feasible[4:]
        if targets is None and stratum_constraints and (budget is None or cost <= budget):
            if not any(_dominates(x,candidate) for x in frontier):
                frontier=[x for x in frontier if not _dominates(candidate,x)]
                frontier.append(candidate)
    if targets is None:
        frontier.sort(key=lambda x:(_cost(x['cost_cny']),-x['projected_lower_six']))
        result.update(status='frontier_only',frontier=frontier,blocked_reasons=['quality_target_not_confirmed'])
    elif feasible:
        result.update(status=spec['evidence_status'],optimal=feasible[0],alternatives=feasible[1:])
    else:
        result.update(status='infeasible',blocked_reasons=[
            'quality_unreachable_under_pilot_scenario' if minimum_quality_cost is None else 'budget_below_cheapest_feasible_quality'])
    result['minimum_cost_meeting_projected_quality']=str(minimum_quality_cost) if minimum_quality_cost is not None else None
    return result


def synthetic_allocation_spec():
    matrix=[[0]*6 for _ in range(6)]
    for i in range(6):
        matrix[i][i]=9
        matrix[i][i^1]=1
    return dict(term='SYNTHETIC_ONLY',evidence_status='synthetic_scenario',alpha=.05,
                targets={'six':.70,'three':.80},budget_cny='200',max_combinations=20000,
                parameter_sources={'all':'Explicit synthetic assumptions; not measured costs or official thresholds'},
                strata=[dict(name=name,frame_size=size,min_reviews=minimum,pilot_confusion=deepcopy(matrix),
                             pilot_prediction_na=0,ai_unit_cny='0.01',double_review_unit_cny='1',
                             adjudication_unit_cny='0.5',disagreement_rate=.1,
                             quality_floor={'six':.5,'three':.6},min_prediction_coverage=.8)
                        for name,size,minimum in [('common',48,4),('rare_prespecified',12,2)]])


def allocation_scenarios():
    scenarios=[]
    for name in ('low_human_cost','medium_human_cost','high_human_cost','higher_error',
                 'higher_na','smaller_rare_stratum','larger_rare_stratum'):
        spec=deepcopy(synthetic_allocation_spec())
        if name.endswith('human_cost'):
            multiplier={'low_human_cost':.5,'medium_human_cost':1,'high_human_cost':2}[name]
            for h in spec['strata']:h['double_review_unit_cny']=str(multiplier)
        elif name=='higher_error':
            for h in spec['strata']:
                for i in range(6):h['pilot_confusion'][i][i],h['pilot_confusion'][i][i^1]=6,4
        elif name=='higher_na':
            for h in spec['strata']:h['pilot_prediction_na']=30
        else:
            spec['strata'][1]['frame_size']=6 if name=='smaller_rare_stratum' else 24
        scenarios.append(dict(name=name,spec=spec,result=solve_allocation(spec)))
    return scenarios
