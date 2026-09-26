"""Audited single-term panel DR-DID with saturated categorical nuisance models.

No automatic identification claim: documented assumptions remain assumptions.
No network, imputation, hidden trimming, or row-level report export.
"""

from collections import defaultdict
from datetime import datetime
import math
import random
import statistics

from .causal_reporting import build_causal_report
from .contracts import finite_number, require
from .causal_diagnostics import panel_diagnostics
from tools.label_noise import percentile


ASSUMPTIONS = ('parallel_trends', 'no_anticipation', 'no_interference')
EVIDENCE = ('assignment', 'comparison', 'outcome', 'covariates', 'population')
ROW_KEYS = {'student_key','cluster_id','term','treated','pre_score','post_score',
            'baseline_time','covariates_time','treatment_time','followup_time','covariates'}


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _time(value):
    require(isinstance(value, str), 'INVALID_TIME')
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        require(False, 'INVALID_TIME')
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None, 'TIMEZONE_REQUIRED')
    return parsed


def import_panel(store, document):
    require(isinstance(document, dict) and set(document) == {'schema_version','synthetic','rows'}, 'INVALID_PANEL')
    require(document['schema_version'] == 'causal-panel-v1' and type(document['synthetic']) is bool, 'INVALID_PANEL_VERSION')
    require(isinstance(document['rows'], list) and bool(document['rows']), 'EMPTY_PANEL')
    identities = set()
    for row in document['rows']:
        require(isinstance(row, dict) and set(row) == ROW_KEYS, 'INVALID_PANEL_ROW')
        require(all(_text(row[k]) for k in ('student_key','cluster_id','term')), 'INVALID_PANEL_ID')
        require(row['student_key'] not in identities, 'DUPLICATE_STUDENT')
        identities.add(row['student_key'])
        require(type(row['treated']) is int and row['treated'] in (0,1), 'INVALID_TREATMENT')
        require(isinstance(row['covariates'], dict) and all(_text(k) and _text(v) for k,v in row['covariates'].items()),
                'CATEGORICAL_COVARIATES_REQUIRED')
        for field in ('pre_score','post_score'):
            if row[field] is not None:
                finite_number(row[field])
        for field in ('baseline_time','covariates_time','treatment_time','followup_time'):
            if row[field] is not None:
                _time(row[field])
    return store.put('causal_panel', document)


def _validate_protocol(p):
    keys = {'schema_version','term','treatment_contrast','time_zero','followup_window','outcome',
            'covariates','frozen_by','design_evidence','assumptions','min_clusters_per_arm',
            'educational_threshold','trend_bias_bounds'}
    require(isinstance(p, dict) and set(p) == keys and p['schema_version'] == 'causal-protocol-v1', 'INVALID_CAUSAL_PROTOCOL')
    require(_text(p['term']) and _text(p['treatment_contrast']) and isinstance(p['frozen_by'],str), 'INVALID_PROTOCOL_TEXT')
    zero = _time(p['time_zero'])
    window = p['followup_window']
    require(isinstance(window,list) and len(window) == 2, 'INVALID_FOLLOWUP_WINDOW')
    require(zero < _time(window[0]) <= _time(window[1]), 'INVALID_FOLLOWUP_WINDOW')
    outcome = p['outcome']
    require(isinstance(outcome,dict) and set(outcome) == {'name','lower','upper','independent','comparable','higher_is_better'},
            'INVALID_OUTCOME_SPEC')
    require(_text(outcome['name']) and all(type(outcome[k]) is bool for k in ('independent','comparable','higher_is_better')),
            'INVALID_OUTCOME_SPEC')
    finite_number(outcome['lower'])
    finite_number(outcome['upper'])
    require(outcome['lower'] < outcome['upper'], 'INVALID_OUTCOME_RANGE')
    finite_number(outcome['upper']-outcome['lower'])
    names = p['covariates']
    require(isinstance(names,list) and all(_text(k) for k in names) and len(set(names)) == len(names), 'INVALID_COVARIATES')
    require(isinstance(p['design_evidence'],dict) and set(p['design_evidence']) == set(EVIDENCE)
            and all(isinstance(v,str) for v in p['design_evidence'].values()), 'INVALID_DESIGN_EVIDENCE')
    require(isinstance(p['assumptions'],dict) and set(p['assumptions']) == set(ASSUMPTIONS), 'INVALID_ASSUMPTIONS')
    for assumption in p['assumptions'].values():
        require(isinstance(assumption,dict) and set(assumption) == {'status','rationale'}
                and assumption['status'] in ('assumed','unverified') and isinstance(assumption['rationale'],str), 'INVALID_ASSUMPTION')
    require(type(p['min_clusters_per_arm']) is int and p['min_clusters_per_arm'] >= 10, 'INVALID_CLUSTER_POLICY')
    finite_number(p['educational_threshold'],0,outcome['upper']-outcome['lower'])
    require(isinstance(p['trend_bias_bounds'],list) and 1 <= len(p['trend_bias_bounds']) <= 20, 'INVALID_BIAS_BOUNDS')
    for bound in p['trend_bias_bounds']:
        finite_number(bound,0,outcome['upper']-outcome['lower'])
    require(len(set(p['trend_bias_bounds'])) == len(p['trend_bias_bounds']), 'DUPLICATE_BIAS_BOUND')


def _strata(rows, names):
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row['covariates'][k] for k in names)].append(row)
    return dict(sorted(groups.items()))


def audit_panel(store, panel_id, protocol):
    store.verify_tree(panel_id)
    document = store.get(panel_id, 'causal_panel')['payload']
    _validate_protocol(protocol)
    rows, p = document['rows'], protocol
    blockers = set()
    if not _text(p['frozen_by']): blockers.add('PROTOCOL_NOT_FROZEN')
    if any(not _text(v) for v in p['design_evidence'].values()): blockers.add('DESIGN_EVIDENCE_MISSING')
    if any(a['status'] != 'assumed' or not _text(a['rationale']) for a in p['assumptions'].values()):
        blockers.add('ASSUMPTION_UNDOCUMENTED')
    if not p['outcome']['independent']: blockers.add('NO_INDEPENDENT_OUTCOME')
    if not p['outcome']['comparable']: blockers.add('OUTCOME_NOT_COMPARABLE')
    if not p['outcome']['higher_is_better']: blockers.add('UNSUPPORTED_OUTCOME_DIRECTION')
    zero = _time(p['time_zero'])
    window = [_time(t) for t in p['followup_window']]
    clusters = defaultdict(set)
    missing = {'pre_score':0,'post_score':0}
    covariates_valid = True
    for r in rows:
        clusters[r['cluster_id']].add(r['treated'])
        if r['term'] != p['term']: blockers.add('TERM_MISMATCH')
        if set(r['covariates']) != set(p['covariates']):
            covariates_valid = False
            blockers.add('COVARIATE_SCHEMA_MISMATCH')
        for key in missing:
            if r[key] is None:
                missing[key] += 1
                blockers.add('MISSING_OUTCOME_NO_STRATEGY')
            elif not p['outcome']['lower'] <= r[key] <= p['outcome']['upper']:
                blockers.add('OUTCOME_OUT_OF_RANGE')
        if r['baseline_time'] is None or r['followup_time'] is None or r['covariates_time'] is None:
            blockers.add('TIME_ZERO_UNVERIFIED')
        else:
            if _time(r['baseline_time']) >= zero: blockers.add('BASELINE_NOT_PRETREATMENT')
            if _time(r['covariates_time']) >= zero: blockers.add('POST_TREATMENT_COVARIATE')
            if not window[0] <= _time(r['followup_time']) <= window[1]: blockers.add('FOLLOWUP_OUTSIDE_WINDOW')
        if r['treated']:
            if r['treatment_time'] is None or _time(r['treatment_time']) != zero:
                blockers.add('TREATMENT_START_MISMATCH')
        elif r['treatment_time'] is not None:
            blockers.add('CONTROL_CONTAMINATION')
    counts = {str(d):sum(r['treated'] == d for r in rows) for d in (0,1)}
    if not counts['0']: blockers.add('NO_VALID_CONTROL')
    if not counts['1']: blockers.add('NO_TREATED_STUDENTS')
    if any(len(v) > 1 for v in clusters.values()): blockers.add('MIXED_TREATMENT_CLUSTER')
    cluster_counts = {str(d):sum(v == {d} for v in clusters.values()) for d in (0,1)}
    summaries = []
    if covariates_valid:
        for index,(key,group) in enumerate(_strata(rows,p['covariates']).items(),1):
            n1 = sum(r['treated'] for r in group)
            n0 = len(group)-n1
            if not n1 or not n0: blockers.add('NO_COMMON_SUPPORT')
            summaries.append(dict(stratum=f'stratum_{index}',covariates=dict(zip(p['covariates'],key)),
                                  treated=n1,control=n0,propensity=n1/len(group)))
    return store.put('causal_audit',dict(panel=panel_id,protocol=p,synthetic=document['synthetic'],
        status='blocked' if blockers else 'ready_under_assumptions',blockers=sorted(blockers),
        students=counts,clusters=cluster_counts,missing_outcomes=missing,strata=summaries,
        diagnostics=panel_diagnostics(rows,p['covariates']) if covariates_valid else None,
        inference_eligible=not blockers and min(cluster_counts.values()) >= p['min_clusters_per_arm'],
        limitations=['Assumption documentation is not verification of causal identification.',
                    'Single-term complete panel only; no imputation or silent complete-case selection.',
                    'Categorical strata must be selected before outcomes; no post-treatment variables.',
                    'Cluster count is an operational screen, not a guarantee of valid inference.']), [panel_id], p)


def _fit(rows, names):
    """Saturated categorical e(X) and control trend m0(X), refit on every draw."""
    n1 = sum(r['treated'] for r in rows)
    n0 = len(rows)-n1
    if not n1 or not n0: return None
    treated_residual, control_residual, control_weights = [], [], []
    for group in _strata(rows,names).values():
        treated = [r for r in group if r['treated']]
        control = [r for r in group if not r['treated']]
        if not treated or not control: return None
        m0 = statistics.mean(r['post_score']-r['pre_score'] for r in control)
        odds = len(treated)/len(control)
        treated_residual.extend(r['post_score']-r['pre_score']-m0 for r in treated)
        control_residual.extend(odds*(r['post_score']-r['pre_score']-m0) for r in control)
        control_weights.extend([odds]*len(control))
    total_weight = math.fsum(control_weights)
    estimate = math.fsum(treated_residual)/n1-math.fsum(control_residual)/total_weight
    naive = (statistics.mean(r['post_score']-r['pre_score'] for r in rows if r['treated'])
             - statistics.mean(r['post_score']-r['pre_score'] for r in rows if not r['treated']))
    sd = statistics.stdev(r['pre_score'] for r in rows) if len(rows) > 1 else 0
    return dict(estimate=estimate,unadjusted_did=naive,baseline_sd=sd,
                standardized_effect=estimate/sd if sd else None,
                control_ess=total_weight**2/math.fsum(w*w for w in control_weights),
                max_control_weight=max(control_weights))


def _bootstrap(rows, names, point, seed, replicates, minimum):
    clusters = [defaultdict(list),defaultdict(list)]
    for row in rows: clusters[row['treated']][row['cluster_id']].append(row)
    units = [list(arm.values()) for arm in clusters]
    if min(map(len,units)) < minimum:
        return dict(ci95=None,p_value=None,inference_status='insufficient_clusters',valid_replicates=0)
    rng = random.Random(seed)
    samples = []
    for _ in range(replicates):
        draw = [row for arm in units for unit in rng.choices(arm,k=len(arm)) for row in unit]
        fitted = _fit(draw,names)
        if fitted is not None: samples.append(fitted['estimate'])
    if len(samples) != replicates:
        return dict(ci95=None,p_value=None,inference_status='bootstrap_support_failure',valid_replicates=len(samples))
    if max(samples)-min(samples) <= 1e-12:
        return dict(ci95=None,p_value=None,inference_status='degenerate_bootstrap',valid_replicates=len(samples))
    p_value = (1+sum(abs(v-point) >= abs(point) for v in samples))/(replicates+1)
    return dict(ci95=[percentile(samples,.025),percentile(samples,.975)],p_value=p_value,
                inference_status='approximate_cluster_bootstrap',valid_replicates=len(samples))


def estimate_effect(store, audit_id, seed=2045, replicates=500):
    require(type(seed) is int and type(replicates) is int and 100 <= replicates <= 10000, 'INVALID_BOOTSTRAP_CONFIG')
    store.verify_tree(audit_id)
    audit = store.get(audit_id,'causal_audit')['payload']
    require(audit['status'] == 'ready_under_assumptions', 'CAUSAL_NOT_IDENTIFIED')
    rows = store.get(audit['panel'],'causal_panel')['payload']['rows']
    p = audit['protocol']
    point = _fit(rows,p['covariates'])
    require(point is not None,'NO_COMMON_SUPPORT')
    inference = _bootstrap(rows,p['covariates'],point['estimate'],seed,replicates,p['min_clusters_per_arm'])
    subgroups = []
    for i,(key,group) in enumerate(_strata(rows,p['covariates']).items(),1):
        fit = _fit(group,p['covariates'])
        interval = _bootstrap(group,p['covariates'],fit['estimate'],seed+i,replicates,p['min_clusters_per_arm'])
        # Subgroup intervals are exploratory and not multiplicity-adjusted; no subgroup significance claims.
        subgroups.append(dict(stratum=f'stratum_{i}',students=len(group),estimate=fit['estimate'],
                              ci95=interval['ci95'],inference_status=interval['inference_status']))
    ci = inference['ci95']
    sensitivity = [dict(bias_bound=b,effect_bounds=[point['estimate']-b,point['estimate']+b],
                        conservative_ci95=[ci[0]-b,ci[1]+b] if ci is not None else None)
                   for b in sorted(p['trend_bias_bounds'])]
    return store.put('causal_effect',dict(audit=audit_id,synthetic=audit['synthetic'],term=p['term'],
        estimand='ATT under documented assumptions',estimator='saturated_categorical_panel_dr_did',
        claim_type='synthetic_validation' if audit['synthetic'] else 'conditional_causal_estimate',
        **point,**inference,students=audit['students'],clusters=audit['clusters'],
        educational_threshold=p['educational_threshold'],
        educational_threshold_exceeded=ci[0] > p['educational_threshold'] if ci is not None else None,
        heterogeneity=subgroups,sensitivity=sensitivity,seed=seed,replicates=replicates,
        limitations=['Identification assumptions are not established by these calculations.',
                    'Stratum-saturated nuisance models; equivalent to treated-composition-weighted stratum DID.',
                    'No flexible nuisance learning or cross-fitting; no continuous covariates.',
                    'Percentile CI and centered two-sided bootstrap p-value are approximate, not exact randomization inference.',
                    'Bootstrap resamples whole clusters within each arm and refits both nuisance models.',
                    'Any bootstrap support failure suppresses inference; invalid replicates are not conditioned away.',
                    'Subgroup estimates are exploratory, not adjusted for multiplicity, and not tests of between-group differences.',
                    'Bias bounds are fixed user assumptions, not measured violations or full HonestDiD inference.',
                    'No estimated label-error or missing-data uncertainty; no student rankings.']), [audit_id],
        dict(seed=seed,replicates=replicates))
