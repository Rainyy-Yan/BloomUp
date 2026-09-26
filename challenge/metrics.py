"""Metrics preserving original conversation endpoints and missingness."""

from collections import defaultdict

from .core import _simplex
from .contracts import require
from .scoring import (INDICATORS, aggregate_indicators, breadth_score, normalized_dhi,
                      transition_quality, validate_metric_spec)


def student_metrics(turns, labels, reference, weights, utilities=None):
    _simplex(reference, 6)
    _simplex(weights, 3)
    known_ids = {x['turn_id'] for x in turns}
    require(len(known_ids)==len(turns), 'DUPLICATE_TURN')
    require(set(labels).issubset(known_ids), 'UNKNOWN_LABEL_ID')
    require(all(v is None or type(v) is int and 1 <= v <= 6 for v in labels.values()), 'INVALID_LABEL')
    grouped = defaultdict(list)
    for turn in turns: grouped[(turn['term'], turn['student_key'])].append(turn)
    results = []
    for (term, student), rows in sorted(grouped.items()):
        conversations = defaultdict(list)
        for row in rows: conversations[row['record_id']].append(row)
        values = [labels[x['turn_id']] for x in rows if labels.get(x['turn_id']) is not None]
        changes, ctqs = [], []
        for conv in conversations.values():
            conv.sort(key=lambda x: x['turn_index'])
            require([x['turn_index'] for x in conv] == list(range(1, len(conv)+1)), 'BROKEN_CONVERSATION_ORDER')
            if len(conv) < 2: continue
            first, last = (labels.get(conv[i]['turn_id']) for i in [0, -1])
            if first is not None and last is not None:
                ctqs.append(0.5+(last-first)/10 if utilities is None else
                            transition_quality([first, last], utilities))
                changes.append(int(last >= 4)-int(first >= 4))
        result = {'term': term, 'student_key': student, 'candidate_turns': len(rows), 'labeled_turns': len(values),
                  'coverage': len(values)/len(rows), 'endpoint_pairs': len(ctqs), 'abl': None, 'hot': None,
                  'dhi': None, 'ctq': sum(ctqs)/len(ctqs) if ctqs else None, 'aiv': None,
                  'observed_hot_change': sum(changes)/len(changes) if changes else None,
                  'identification_bounds': None, 'sampling_ci': None, 'label_sensitivity_interval': None,
                  'claim_type': 'descriptive', 'analysis_status': 'exploratory', 'rank': None}
        if values:
            p = [values.count(i)/len(values) for i in range(1, 7)]
            result.update(abl=sum(values)/len(values), hot=sum(p[3:]), dhi=1-sum(abs(a-b) for a,b in zip(p,reference))/2)
            base = 100*(weights[0]*result['hot']+weights[2]*result['dhi'])
            if ctqs: result['aiv'] = base+100*weights[1]*result['ctq']
            else: result['identification_bounds'] = [base, base+100*weights[1]]
        result['missing_reasons'] = ([] if values else ['no_valid_labels']) + ([] if ctqs else ['no_valid_original_endpoints'])
        agent_ids = {x['agent_id'] for x in rows if x.get('agent_id')}
        result['mab'] = min(len(agent_ids), 3)/3 if all(x.get('agent_id') for x in rows) else None
        result['mab_missing_reason'] = 'unknown_agent_identity' if result['mab'] is None else None
        results.append(result)
    return results


def calculate_students(turns, labels, spec):
    """Dispatch explicitly versioned formulas; never reinterpret a legacy spec."""
    version = validate_metric_spec(spec)
    if version == 'legacy-v1':
        return student_metrics(turns, labels, spec['reference_distribution'], spec['weights'])
    grouped = defaultdict(list)
    catalog = spec['agent_catalog']
    for turn in turns:
        agent = turn.get('agent_id')
        require(agent is None or isinstance(agent, str), 'INVALID_AGENT_ID')
        if agent and catalog is not None:
            require(agent in catalog, 'AGENT_OUTSIDE_FROZEN_CATALOG')
        grouped[(turn['term'], turn['student_key'])].append(turn)
    results = student_metrics(turns, labels, spec['reference_distribution'], [0.5, 0.3, 0.2], spec['utilities'])
    for result in results:
        rows = grouped[(result['term'], result['student_key'])]
        values = [labels[t['turn_id']] for t in rows if labels.get(t['turn_id']) is not None]
        result.update(formula_version=version, dhi_symmetric=None, dhi_asymmetric=None)
        if values:
            result['hot'] = sum(level >= 4 for level in values) / len(values)
            p = [values.count(k) / len(values) for k in range(1, 7)]
            result['dhi_symmetric'] = normalized_dhi(p, spec['reference_distribution'])
            result['dhi_asymmetric'] = normalized_dhi(p, spec['reference_distribution'],
                                                     spec['shortage_penalties'], spec['excess_penalties'])
        result['dhi'] = result['dhi_' + spec['dhi_mode']]
        reason = ('unknown_agent_catalog' if catalog is None else
                  'unknown_agent_identity' if any(not t.get('agent_id') for t in rows) else None)
        result['mab_missing_reason'] = reason
        result['mab'] = None if reason else breadth_score(len({t['agent_id'] for t in rows}), len(catalog))
        score = aggregate_indicators([result[k] for k in INDICATORS], spec['weights'], spec['aggregation'])
        result['aiv'] = score['aiv']
        result['identification_bounds'] = score['identification_bounds']
        result['aiv_contributions'] = dict(zip(INDICATORS, score['contributions']))
        result['bounds_conditioning'] = 'available_label_metrics_and_observed_original_endpoints_fixed'
        if reason:
            result['missing_reasons'].append(reason)
    return results
