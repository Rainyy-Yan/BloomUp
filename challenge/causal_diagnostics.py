"""Descriptive overlap, weight and pre-treatment balance diagnostics.

Thresholds flag review needs; they neither establish nor reject causal identification.
"""

from collections import defaultdict
import math
import statistics


def _balance(feature, category, treated, control, weights):
    observed_t = [v for v in treated if v is not None]
    observed_c = [v for v in control if v is not None]
    mt = statistics.mean(observed_t) if observed_t else None
    mc = statistics.mean(observed_c) if observed_c else None
    complete = len(observed_t) == len(treated) and len(observed_c) == len(control)
    weighted = (math.fsum(v*w for v,w in zip(control,weights))/math.fsum(weights)
                if complete and control and weights is not None else None)
    scale = None
    status = 'insufficient_or_missing_baseline'
    if complete and len(treated) >= 2 and len(control) >= 2:
        variance = ((mt*(1-mt)+mc*(1-mc))/2 if category is not None else
                    (statistics.variance(treated)+statistics.variance(control))/2)
        scale = math.sqrt(variance)
        status = 'positive_variance' if scale else 'constant_equal' if mt == mc else 'zero_variance_imbalance'
    before = (mt-mc)/scale if scale else 0.0 if status == 'constant_equal' else None
    after = None
    if weighted is not None and status != 'insufficient_or_missing_baseline':
        after = (mt-weighted)/scale if scale else 0.0 if mt == weighted else None
    return dict(feature=feature,category=category,treated_available=len(observed_t),control_available=len(observed_c),
                mean_treated=mt,mean_control=mc,weighted_control_mean=weighted,
                fixed_unweighted_scale=scale,scale_status=status,smd_before=before,smd_after=after)


def panel_diagnostics(rows, names):
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row['covariates'][k] for k in names)].append(row)
    counts = {key:(sum(r['treated'] for r in group),sum(not r['treated'] for r in group))
              for key,group in groups.items()}
    supported = bool(counts) and all(n1 and n0 for n1,n0 in counts.values())
    treated = [r for r in rows if r['treated']]
    control = [r for r in rows if not r['treated']]
    weights = None
    weight_summary = None
    warnings = []
    if supported:
        weights = [counts[tuple(r['covariates'][k] for k in names)][0]
                   /counts[tuple(r['covariates'][k] for k in names)][1] for r in control]
        control_clusters = defaultdict(float)
        treated_clusters = defaultdict(int)
        for row,w in zip(control,weights): control_clusters[row['cluster_id']] += w
        for row in treated: treated_clusters[row['cluster_id']] += 1
        total = math.fsum(weights)
        weight_summary = dict(control_ess=total**2/math.fsum(w*w for w in weights),
            control_cluster_ess=total**2/math.fsum(w*w for w in control_clusters.values()),
            treated_cluster_ess=len(treated)**2/math.fsum(v*v for v in treated_clusters.values()),
            max_control_weight=max(weights),max_control_cluster_share=max(control_clusters.values())/total,
            max_treated_cluster_share=max(treated_clusters.values())/len(treated),trimming_applied=False)
        if max(weight_summary['max_control_cluster_share'],weight_summary['max_treated_cluster_share']) > .2:
            warnings.append('CLUSTER_WEIGHT_CONCENTRATION')
    else:
        warnings.append('UNSUPPORTED_STRATA_NO_WEIGHTS')
    propensity = [n1/(n1+n0) for n1,n0 in counts.values()]
    extreme = sum(e < .05 or e > .95 for e in propensity)
    if extreme: warnings.append('EXTREME_PROPENSITY_STRATA')
    balance = [_balance('pre_score',None,[r['pre_score'] for r in treated],[r['pre_score'] for r in control],weights)]
    for name in names:
        for value in sorted({r['covariates'][name] for r in rows}):
            balance.append(_balance(name,value,[int(r['covariates'][name] == value) for r in treated],
                                    [int(r['covariates'][name] == value) for r in control],weights))
    if any(r['smd_after'] is not None and abs(r['smd_after']) > .1 or
           r['scale_status'] == 'zero_variance_imbalance' for r in balance):
        warnings.append('RESIDUAL_BASELINE_IMBALANCE')
    if any(r['scale_status'] == 'insufficient_or_missing_baseline' for r in balance):
        warnings.append('BALANCE_INCOMPLETE')
    return dict(overlap=dict(propensity_min=min(propensity),propensity_max=max(propensity),
                             strata=len(counts),extreme_strata=extreme,
                             unsupported_students=sum(sum(counts[k]) for k in counts if not all(counts[k]))),
                weights=weight_summary,balance=balance,warnings=warnings,
                thresholds=dict(abs_smd=.1,propensity_range=[.05,.95],max_cluster_share=.2),
                limits=['Diagnostic thresholds are review heuristics, not identification or inference guarantees.',
                        'Post-weighting balance uses a fixed pre-weighting denominator; zero variance is not silently scaled.',
                        'Exact balance of fitted strata is mechanical; baseline score and unmeasured confounding may remain imbalanced.',
                        'Missing baseline measurements suppress standardized and weighted comparisons; availability is reported.',
                        'Weights are never clipped and observations are never dropped.'])
