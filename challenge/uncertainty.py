"""Versioned, explicitly assumed label-noise scenarios."""

from collections import defaultdict
import random
import statistics

from tools.label_noise import assumed_neighbour_kernel, percentile

from .contracts import finite_number, require
from .metrics import student_metrics


METRICS = ('abl', 'hot', 'ctq', 'dhi', 'aiv')


def _means(rows):
    result = {}
    for key in METRICS:
        values = [row[key] for row in rows if row[key] is not None]
        result[key] = statistics.mean(values) if values else None
    return result


def _interval(values):
    return [percentile(values, .025), percentile(values, .975)] if values else None


def _perturb(level, kernel, draw):
    cumulative = 0.0
    for candidate, probability in kernel[level]:
        cumulative += probability
        if draw < cumulative:
            return candidate
    return kernel[level][-1][0]


def label_sensitivity(store, metrics_id, error_masses=(0, .1, .2, .3), seed=2045, replicates=1000):
    """Hold missingness fixed; distinguish label-only and joint bootstrap ranges.

    Within each term, perturb every known original label once per replication,
    then reuse that realization when resampling whole correlated components.
    The same seed gives common draws across assumed error masses. No ranking,
    row-level data, or student identifiers are written to this artifact.
    """
    store.verify_tree(metrics_id)
    require(type(seed) is int and type(replicates) is int and 100 <= replicates <= 10000,
            'INVALID_SCENARIO_CONFIG')
    require(isinstance(error_masses, (list, tuple)) and 1 <= len(error_masses) <= 10, 'INVALID_ERROR_MASSES')
    for mass in error_masses:
        finite_number(mass, 0, 1)
    require(len(set(error_masses)) == len(error_masses), 'DUPLICATE_ERROR_MASS')
    metric = store.get(metrics_id, 'metrics')['payload']
    labels = store.get(metric['labels'], 'labels')['payload']
    require(labels['dataset'] == metric['dataset'], 'LABEL_VERSION_MISMATCH')
    dataset = store.get(metric['dataset'], 'dataset')['payload']
    index = {row['turn_id']: row['label'] for row in labels['rows']}
    require(any(value is not None for value in index.values()), 'NO_VALID_LABELS')
    terms = defaultdict(list)
    for turn in dataset['turns']:
        if turn['turn_id'] in index:
            terms[turn['term']].append(turn)
    component_by_student = dataset['sampling']['component_by_student']
    spec = metric['spec']
    results = []
    excluded_terms = []
    for term, turns in sorted(terms.items()):
        turns.sort(key=lambda row: row['turn_id'])
        term_labels = {turn['turn_id']: index[turn['turn_id']] for turn in turns}
        known = [value for value in term_labels.values() if value is not None]
        if not known:
            excluded_terms.append({'term': term, 'reason': 'no_valid_labels'})
            continue
        baseline = student_metrics(turns, term_labels, spec['reference_distribution'], spec['weights'])
        baseline = [row for row in baseline if row['labeled_turns'] > 0]
        point = _means(baseline)
        clusters = defaultdict(list)
        for row in baseline:
            student = row['student_key']
            require(student in component_by_student, 'MISSING_COMPONENT')
            clusters[component_by_student[student]].append(student)
        units = [clusters[key] for key in sorted(clusters)]
        support = {key: len({component_by_student[row['student_key']] for row in baseline if row[key] is not None})
                   for key in METRICS}
        for mass in sorted(error_masses):
            kernel = assumed_neighbour_kernel(known, mass)
            noise_rng = random.Random(seed)
            bootstrap_rng = random.Random(seed + 1)
            label_series = {key: [] for key in METRICS}
            joint_series = {key: [] for key in METRICS}
            for _ in range(replicates):
                perturbed = {tid: None if level is None else _perturb(level, kernel, noise_rng.random())
                             for tid, level in term_labels.items()}
                rows = student_metrics(turns, perturbed, spec['reference_distribution'], spec['weights'])
                by_student = {row['student_key']: row for row in rows}
                label_means = _means(rows)
                drawn = [by_student[student] for unit in bootstrap_rng.choices(units, k=len(units)) for student in unit]
                joint_means = _means(drawn)
                for key in METRICS:
                    if label_means[key] is not None:
                        label_series[key].append(label_means[key])
                    if joint_means[key] is not None:
                        joint_series[key].append(joint_means[key])
            summaries = {}
            for key in METRICS:
                # Do not silently condition the joint interval on draws with observed endpoints.
                enough = support[key] >= 5 and len(joint_series[key]) == replicates
                summaries[key] = {
                    'observed': point[key],
                    'students': sum(row[key] is not None for row in baseline),
                    'supporting_components': support[key],
                    'perturbation_interval_95': _interval(label_series[key]),
                    'joint_resampling_interval_95': _interval(joint_series[key]) if enough else None,
                    'joint_valid_replicates': len(joint_series[key]),
                    'joint_missing_reason': None if enough else 'fewer_than_five_supporting_components_or_missing_bootstrap_estimates',
                }
            results.append({'term': term, 'error_mass': mass, 'students': len(baseline),
                            'components': len(units), 'known_turns': len(known), 'metrics': summaries})
    config = {'error_masses': sorted(error_masses), 'seed': seed, 'replicates': replicates}
    return store.put('label_sensitivity', {
        'metrics': metrics_id, 'dataset': metric['dataset'], 'labels': metric['labels'], 'rows': results,
        'excluded_terms': excluded_terms, 'config': config, 'synthetic': dataset['synthetic'],
        'assumption_status': 'hypothetical_not_estimated', 'claim_type': 'sensitivity',
        'method': 'Fixed observed prevalence with 0.5 smoothing allocates assumed adjacent error; equal-student term means.',
        'limits': ['Error mass is a scenario input, not accuracy or an estimated annotator confusion matrix.',
                   'Perturbation ranges condition on observed labels; joint ranges also resample within-term components.',
                   'Ranges are Monte Carlo scenario quantiles, not causal confidence intervals.',
                   'Missing labels stay missing; CTQ always uses original endpoints; no ranking is generated.',
                   'No cross-term pairing; dependencies beyond preparation components are not modeled.'],
    }, [metrics_id], config)
