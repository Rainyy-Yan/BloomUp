"""Metrics preserving original conversation endpoints and missingness."""

from collections import defaultdict

from .core import _simplex
from .contracts import require


def student_metrics(turns, labels, reference, weights):
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
                ctqs.append(0.5+(last-first)/10)
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
