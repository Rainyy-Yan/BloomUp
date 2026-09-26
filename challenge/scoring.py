"""Mathematical AIV v2 contracts, normalized indicators and aggregate bounds."""

import math

from .contracts import finite_number, require
from .core import _simplex


INDICATORS = ('hot', 'ctq', 'dhi', 'mab')
PRIORITY_WEIGHTS = (.5, .3, .15, .05)
BALANCED_WEIGHTS = (.3, .3, .3, .1)


def _utilities(values):
    require(isinstance(values, (list, tuple)) and len(values) == 6, 'INVALID_UTILITIES')
    for value in values:
        finite_number(value, 0, 1)
    require(values[0] == 0 and values[-1] == 1 and all(a < b for a, b in zip(values, values[1:])),
            'UTILITIES_MUST_STRICTLY_INCREASE_FROM_ZERO_TO_ONE')


def _penalties(values):
    require(isinstance(values, (list, tuple)) and len(values) == 6, 'INVALID_PENALTIES')
    for value in values:
        finite_number(value, 0)
        require(value > 0, 'PENALTIES_MUST_BE_POSITIVE')


def _aggregation(config):
    require(isinstance(config, dict), 'INVALID_AGGREGATION')
    if config.get('kind') == 'linear':
        require(set(config) == {'kind'}, 'INVALID_AGGREGATION_FIELDS')
        return
    require(set(config) == {'kind', 'rho'} and config['kind'] == 'concave', 'INVALID_AGGREGATION')
    finite_number(config['rho'], 0, 50)
    require(config['rho'] > 0, 'RHO_MUST_BE_POSITIVE')


def transform(value, aggregation):
    finite_number(value, 0, 1)
    _aggregation(aggregation)
    if aggregation['kind'] == 'linear':
        return value
    rho = aggregation['rho']
    return -math.expm1(-rho * value) / -math.expm1(-rho)


def normalized_dhi(distribution, reference, shortage=None, excess=None):
    _simplex(distribution, 6)
    _simplex(reference, 6)
    require(all(q > 0 for q in reference), 'REFERENCE_MUST_BE_INTERIOR')
    if shortage is None and excess is None:
        distance = math.fsum(abs(p-q) for p, q in zip(distribution, reference)) / 2
        score = 1 - distance / (1-min(reference))
    else:
        _penalties(shortage)
        _penalties(excess)
        # Common scaling leaves the ratio unchanged and prevents overflowing finite penalties.
        scale = max(*shortage, *excess)
        a, b = [v/scale for v in shortage], [v/scale for v in excess]
        require(all(v > 0 for v in a+b), 'PENALTY_DYNAMIC_RANGE_TOO_LARGE')
        distance = math.fsum(x*max(q-p, 0)+y*max(p-q, 0)
                             for p, q, x, y in zip(distribution, reference, a, b))
        maximum = max(math.fsum(a[k]*reference[k] for k in range(6) if k != r)
                      + b[r]*(1-reference[r]) for r in range(6))
        score = 1 - distance/maximum
    return min(1.0, max(0.0, score))  # Roundoff only; input domains were checked above.


def transition_quality(levels, utilities):
    _utilities(utilities)
    require(all(level is None or type(level) is int and 1 <= level <= 6 for level in levels), 'INVALID_LABEL')
    if len(levels) < 2 or levels[0] is None or levels[-1] is None:
        return None
    return .5 + .5*(utilities[levels[-1]-1] - utilities[levels[0]-1])


def breadth_score(count, maximum):
    require(type(count) is int and type(maximum) is int and maximum >= 1 and 0 <= count <= maximum,
            'INVALID_AGENT_COUNT_OR_MAXIMUM')
    return math.log1p(count)/math.log1p(maximum)


def aggregate_indicators(values, weights, aggregation):
    _simplex(weights, 4)
    _aggregation(aggregation)
    require(len(values) == 4, 'INVALID_INDICATOR_COUNT')
    for value in values:
        if value is not None:
            finite_number(value, 0, 1)
    contributions = [0.0 if weight == 0 else None if value is None else 100*weight*transform(value, aggregation)
                     for value, weight in zip(values, weights)]
    missing_weight = math.fsum(weight for value, weight in zip(values, weights) if value is None)
    lower = min(100.0, math.fsum(value for value in contributions if value is not None))
    if any(value is None for value in contributions):
        return {'aiv': None, 'identification_bounds': [lower, min(100.0, lower+100*missing_weight)],
                'contributions': contributions}
    return {'aiv': lower, 'identification_bounds': None, 'contributions': contributions}


def weight_stability_bound(left, right):
    _simplex(left, 4)
    _simplex(right, 4)
    return 50*math.fsum(abs(a-b) for a, b in zip(left, right))


def feature_stability_bound(errors, weights, aggregation):
    _simplex(weights, 4)
    _aggregation(aggregation)
    require(len(errors) == 4, 'INVALID_INDICATOR_COUNT')
    for error in errors:
        finite_number(error, -1, 1)
    lipschitz = 1 if aggregation['kind'] == 'linear' else aggregation['rho'] / -math.expm1(-aggregation['rho'])
    return 100*lipschitz*math.fsum(weight*abs(error) for error, weight in zip(errors, weights))


def validate_metric_spec(spec):
    require(isinstance(spec, dict), 'INVALID_METRIC_SPEC')
    legacy = {'reference_distribution', 'weights', 'reference_status'}
    if set(spec) == legacy:
        _simplex(spec['reference_distribution'], 6)
        _simplex(spec['weights'], 3)
        require(spec['reference_status'] in ['provisional', 'confirmed'], 'INVALID_REFERENCE_STATUS')
        return 'legacy-v1'
    require(set(spec) == legacy | {'formula_version', 'reference_rationale', 'utilities', 'dhi_mode',
                                   'shortage_penalties', 'excess_penalties', 'agent_catalog',
                                   'agent_catalog_status', 'aggregation'}, 'INVALID_METRIC_SPEC')
    require(spec['formula_version'] == 'aiv-v2', 'UNKNOWN_FORMULA_VERSION')
    _simplex(spec['reference_distribution'], 6)
    require(all(q > 0 for q in spec['reference_distribution']), 'REFERENCE_MUST_BE_INTERIOR')
    require(spec['reference_status'] in ['provisional', 'confirmed'], 'INVALID_REFERENCE_STATUS')
    require(isinstance(spec['reference_rationale'], str) and spec['reference_rationale'].strip(), 'REFERENCE_RATIONALE_REQUIRED')
    _utilities(spec['utilities'])
    require(spec['dhi_mode'] in ['symmetric', 'asymmetric'], 'INVALID_DHI_MODE')
    _penalties(spec['shortage_penalties'])
    _penalties(spec['excess_penalties'])
    catalog = spec['agent_catalog']
    if catalog is None:
        require(spec['agent_catalog_status'] == 'unknown', 'CATALOG_STATUS_MISMATCH')
    else:
        require(isinstance(catalog, list) and bool(catalog)
                and all(isinstance(agent, str) and agent.strip() for agent in catalog), 'INVALID_AGENT_CATALOG')
        require(len(set(catalog)) == len(catalog), 'DUPLICATE_AGENT_IN_CATALOG')
        require(spec['agent_catalog_status'] in ['provisional', 'confirmed'], 'INVALID_CATALOG_STATUS')
    _simplex(spec['weights'], 4)
    _aggregation(spec['aggregation'])
    return 'aiv-v2'
