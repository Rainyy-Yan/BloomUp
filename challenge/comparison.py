"""Within-term common-cohort comparisons without publishing individual ranks."""

from collections import defaultdict
import math

from .contracts import require
from .scoring import (INDICATORS, PRIORITY_WEIGHTS, BALANCED_WEIGHTS,
                      aggregate_indicators, validate_metric_spec, weight_stability_bound)


def _ranks(values):
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0]*len(values)
    start = 0
    while start < len(order):
        end = start+1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        for index in order[start:end]:
            ranks[index] = (start+end-1)/2
        start = end
    return ranks


def spearman(left, right):
    """Pearson correlation of average ranks; undefined for constant/small samples."""
    require(len(left) == len(right), 'UNEQUAL_COMPARISON_LENGTHS')
    if len(left) < 2:
        return None
    x, y = _ranks(left), _ranks(right)
    center = (len(left)-1)/2
    x, y = [v-center for v in x], [v-center for v in y]
    scale = math.sqrt(math.fsum(v*v for v in x)*math.fsum(v*v for v in y))
    return max(-1.0, min(1.0, math.fsum(a*b for a,b in zip(x,y))/scale)) if scale else None


def compare_schemes(rows, spec):
    require(validate_metric_spec(spec) == 'aiv-v2', 'V2_SPEC_REQUIRED')
    base, aggregation = spec['weights'], spec['aggregation']
    scenarios = [('primary', base, aggregation),
                 ('high_order_linear', PRIORITY_WEIGHTS, {'kind':'linear'}),
                 ('balanced_linear', BALANCED_WEIGHTS, {'kind':'linear'}),
                 ('diminishing_returns', PRIORITY_WEIGHTS, {'kind':'concave','rho':1})]
    for i in range(4):
        for multiplier in (.9, 1.1):
            weights = [w*(multiplier if j == i else 1) for j,w in enumerate(base)]
            scenarios.append((f'w{i+1}_x{multiplier}', [w/sum(weights) for w in weights], aggregation))
    terms = defaultdict(list)
    for row in rows:
        terms[row['term']].append(row)
    results, correlations = [], []
    for term, candidates in sorted(terms.items()):
        eligible = [r for r in candidates if all(r[k] is not None for k in INDICATORS)]
        features = [[r[k] for k in INDICATORS] for r in eligible]
        primary = [aggregate_indicators(x, base, aggregation)['aiv'] for x in features]
        for i, left in enumerate(INDICATORS):
            for right in INDICATORS[i+1:]:
                correlations.append(dict(term=term, left=left, right=right, students=len(eligible),
                                         spearman=spearman([r[left] for r in eligible], [r[right] for r in eligible])))
        for name, weights, function in scenarios:
            scores = [aggregate_indicators(x, weights, function)['aiv'] for x in features]
            results.append(dict(term=term, scenario=name, weights=list(weights), aggregation=function,
                                students=len(scores), excluded_students=len(candidates)-len(scores),
                                mean_aiv=math.fsum(scores)/len(scores) if scores else None,
                                rank_correlation_with_primary=spearman(primary, scores),
                                max_absolute_score_change=max((abs(a-b) for a,b in zip(primary,scores)), default=None),
                                weight_change_bound=weight_stability_bound(base, weights) if function == aggregation else None))
    return dict(formula_version='aiv-v2', rows=results, indicator_correlations=correlations,
                claim_type='descriptive',
                limits=['Within each term all scenarios use the same four-indicator complete cohort.',
                        'Average ranks handle exact ties; correlation is undefined for constant scores or n < 2.',
                        'Rank correlations are aggregate diagnostics, not published student rankings or uncertainty intervals.',
                        'Weight bounds apply only with the same transform and frozen features.',
                        'No label-noise, missing-label, reference-target or opportunity-equivalence guarantees.'])
