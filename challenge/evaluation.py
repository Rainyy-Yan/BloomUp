"""Offline quality gates and descriptive, component-bootstrap analysis."""

from collections import defaultdict
import random
import statistics

from .contracts import require, finite_number


def agreement(pairs):
    """Pairs are (gold, prediction, positive design weight); nulls excluded by caller."""
    matrix = [[0.0]*6 for _ in range(6)]
    for gold, prediction, weight in pairs:
        require(type(gold) is int and type(prediction) is int and 1 <= gold <= 6 and 1 <= prediction <= 6, 'INVALID_LABEL')
        finite_number(weight, 0)
        require(weight > 0, 'INVALID_DESIGN_WEIGHT')
        matrix[gold-1][prediction-1] += weight
    total = sum(map(sum, matrix))
    if not total:
        return dict(confusion=matrix, accuracy=None, kappa=None, linear_kappa=None, macro_f1=None)
    rows, cols = list(map(sum, matrix)), [sum(row[i] for row in matrix) for i in range(6)]
    accuracy = sum(matrix[i][i] for i in range(6))/total
    chance = sum(rows[i]*cols[i] for i in range(6))/total**2
    observed = sum(abs(i-j)/5*matrix[i][j] for i in range(6) for j in range(6))/total
    expected = sum(abs(i-j)/5*rows[i]*cols[j] for i in range(6) for j in range(6))/total**2
    # Fixed six-class macro average: unsupported classes contribute zero, explicitly documented.
    f1 = [2*matrix[i][i]/(rows[i]+cols[i]) if rows[i]+cols[i] else 0 for i in range(6)]
    return dict(confusion=matrix, accuracy=accuracy, kappa=(accuracy-chance)/(1-chance) if chance < 1-1e-12 else None,
                linear_kappa=1-observed/expected if expected > 1e-12 else None, macro_f1=sum(f1)/6)


def evaluate_quality(store, predictions_id, gold_id, policy):
    for aid in [predictions_id, gold_id]: store.verify_tree(aid)
    pred, gold = (store.get(aid, kind)['payload'] for aid, kind in [(predictions_id,'predictions'),(gold_id,'gold')])
    require(all(pred[k] == gold[k] for k in ['dataset','rubric']), 'QUALITY_VERSION_MISMATCH')
    require(set(policy) == {'min_n','min_coverage','min_linear_kappa','audit_unseen','attested_by'}, 'INVALID_QUALITY_POLICY')
    if policy['min_n'] is not None:
        require(type(policy['min_n']) is int and policy['min_n'] >= 2, 'INVALID_MIN_N')
    if policy['min_coverage'] is not None:
        finite_number(policy['min_coverage'], 0, 1)
    if policy['min_linear_kappa'] is not None:
        finite_number(policy['min_linear_kappa'], -1, 1)
    require(type(policy['audit_unseen']) is bool, 'INVALID_AUDIT_ATTESTATION')
    require(isinstance(policy['attested_by'], str) and policy['attested_by'].strip(), 'ATTESTATION_REQUIRED')
    dataset = store.get(pred['dataset'], 'dataset')['payload']
    index = {x['turn_id']: x for x in pred['rows']}
    require({x['turn_id'] for x in gold['rows']} == set(dataset['sampling']['selected'][gold['pool']]), 'QUALITY_SAMPLE_CHANGED')
    pairs, weighted, null_gold, missing_pred = [], [], 0, 0
    for row in gold['rows']:
        tid = row['turn_id']
        require(tid in index, 'PREDICTION_MISSING_AUDIT_ID')
        if row['label'] is None:
            null_gold += 1
            continue
        if index[tid]['label'] is None:
            missing_pred += 1
            continue
        pair = (row['label'], index[tid]['label'])
        pairs.append((*pair, 1))
        weight = dataset['sampling']['selection_info'][tid]['design_weight']
        if weight is not None: weighted.append((*pair, weight))
    raw, adjusted = agreement(pairs), agreement(weighted)
    coverage = len(pairs)/(len(pairs)+missing_pred) if pairs or missing_pred else 0
    reasons = []
    if any(policy[k] is None for k in ('min_n','min_coverage','min_linear_kappa')):
        reasons.append('quality_criteria_not_confirmed')
    if gold['pool'] != 'audit' or not policy['audit_unseen']: reasons.append('not_attested_independent_audit')
    if policy['min_n'] is not None and len(pairs) < policy['min_n']: reasons.append('insufficient_pairs')
    if policy['min_coverage'] is not None and coverage < policy['min_coverage']: reasons.append('insufficient_prediction_coverage')
    if len(weighted) != len(pairs): reasons.append('missing_design_weights')
    if adjusted['linear_kappa'] is None or (policy['min_linear_kappa'] is not None and adjusted['linear_kappa'] < policy['min_linear_kappa']):
        reasons.append('insufficient_linear_kappa')
    return store.put('quality', {'dataset':pred['dataset'], 'rubric':pred['rubric'], 'predictions':predictions_id,
                                'gold':gold_id, 'policy':policy, 'paired_n':len(pairs), 'gold_na':null_gold,
                                'prediction_na':missing_pred, 'coverage':coverage, 'unweighted':raw, 'design_weighted':adjusted,
                                'passed':not reasons, 'blocked_reasons':reasons, 'sampling_ci':None,
                                'limits':['Thresholds are user-confirmed operational criteria, not official or statistical guarantees.',
                                          'Weights are preparation design weights; no exact complex-survey variance.',
                                          'Audit independence is attested, not technically provable.',
                                          'Macro F1 averages all six classes; unsupported classes count as zero.']},
                     [predictions_id,gold_id], policy)


def observed_analysis(store, metrics_id, seed=20260925, replicates=1000):
    store.verify_tree(metrics_id)
    require(type(seed) is int and type(replicates) is int and 100 <= replicates <= 10000, 'INVALID_BOOTSTRAP_CONFIG')
    metrics = store.get(metrics_id, 'metrics')['payload']
    dataset = store.get(metrics['dataset'], 'dataset')['payload']
    components = dataset['sampling']['component_by_student']
    by_term = defaultdict(list)
    for row in metrics['rows']:
        if row['observed_hot_change'] is not None: by_term[row['term']].append(row)
    results = []
    for term, rows in sorted(by_term.items()):
        clusters = defaultdict(list)
        for row in rows:
            require(row['student_key'] in components, 'MISSING_COMPONENT')
            clusters[components[row['student_key']]].append(row['observed_hot_change'])
        values = [r['observed_hot_change'] for r in rows]
        interval = None
        if len(clusters) >= 5:
            rng = random.Random(seed)
            units = [clusters[k] for k in sorted(clusters)]
            samples = sorted(statistics.mean(v for unit in rng.choices(units, k=len(units)) for v in unit) for _ in range(replicates))
            interval = [samples[int(.025*(replicates-1))], samples[int(.975*(replicates-1))]]
        results.append({'term':term, 'students':len(rows), 'components':len(clusters),
                        'mean_observed_hot_change':statistics.mean(values), 'sampling_ci_95':interval,
                        'ci_missing_reason':None if interval else 'fewer_than_five_components'})
    return store.put('observed', {'dataset':metrics['dataset'], 'metrics':metrics_id, 'rows':results,
                                 'claim_type':'descriptive', 'seed':seed, 'replicates':replicates,
                                 'method':'Within-term component percentile bootstrap; students have equal point-estimate weight.',
                                 'limits':['Conditional on admitted records and available endpoint labels.',
                                           'Not label-error propagation, a causal estimate, or a probability-sample population interval.',
                                           'No cross-term paired comparison.']}, [metrics_id], {'seed':seed,'replicates':replicates})
