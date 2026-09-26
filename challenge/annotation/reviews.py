"""Version-bound blind review tasks, imports and explicit adjudication."""

import csv
from datetime import datetime
from pathlib import Path
import uuid

from ..contracts import require, turn_hash, label_check, file_hash
from ..pipeline import write_csv, _csv_value


EDITABLE = ['bloom_level', 'evidence_quote', 'outsourcing', 'insufficient_evidence',
            'confidence_1_to_5', 'reviewer_id', 'reviewed_at', 'notes']
IMMUTABLE = ['turn_id', 'dataset_id', 'rubric_id', 'text_hash', 'question', 'prior_context']


def register_rubric(store, path, frozen_by=None):
    text = Path(path).read_text(encoding='utf-8-sig')
    require(text.strip(), 'EMPTY_RUBRIC')
    require(frozen_by is None or isinstance(frozen_by, str) and frozen_by.strip(), 'INVALID_FREEZE_ACTOR')
    return store.put('rubric', {'text': text, 'status': 'frozen' if frozen_by else 'draft', 'frozen_by': frozen_by})


def create_task(store, dataset_id, rubric_id, pool, reviewer):
    for aid in [dataset_id,rubric_id]: store.verify_tree(aid)
    dataset = store.get(dataset_id, 'dataset')['payload']
    rubric = store.get(rubric_id, 'rubric')['payload']
    require(pool in ['development', 'audit', 'risk'], 'INVALID_POOL')
    require(isinstance(reviewer, str) and reviewer.strip(), 'REVIEWER_REQUIRED')
    require(pool != 'audit' or rubric['status'] == 'frozen', 'RUBRIC_NOT_FROZEN')
    selected = dataset['sampling']['selected'][pool]
    require(selected, 'EMPTY_SAMPLE')
    index = {x['turn_id']: x for x in dataset['turns']}
    require(set(selected).issubset(index), 'UNKNOWN_SAMPLE_ID')
    rows = [{'turn_id': tid, 'dataset_id': dataset_id, 'rubric_id': rubric_id,
             'text_hash': turn_hash(index[tid]), 'question': index[tid]['question'],
             'prior_context': index[tid]['prior_context']} for tid in selected]
    aid = store.put('review_task', {'dataset': dataset_id, 'rubric': rubric_id, 'pool': pool,
                                   'reviewer': reviewer, 'rows': rows}, [dataset_id, rubric_id])
    workspace = store.root/'workspaces'/uuid.uuid4().hex
    workspace.mkdir(parents=True, exist_ok=False)
    write_csv(workspace/'review.csv', [dict(x, reviewer_id=reviewer) for x in rows], IMMUTABLE+EDITABLE)
    (workspace/'task_id.txt').write_text(aid+'\n', encoding='utf-8')
    return {'task_id': aid, 'file': str(workspace/'review.csv')}


def import_review(store, task_id, path):
    store.verify_tree(task_id)
    task = store.get(task_id, 'review_task')['payload']
    index = {x['turn_id']: x for x in task['rows']}
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        require(reader.fieldnames == IMMUTABLE+EDITABLE, 'REVIEW_COLUMNS_CHANGED')
        rows = list(reader)
    require(len(rows) == len(index) and {x.get('turn_id') for x in rows} == set(index), 'REVIEW_SAMPLE_CHANGED')
    result = []
    for row in rows:
        require(None not in row and all(x is not None for x in row.values()), 'MALFORMED_CSV')
        original = index[row['turn_id']]
        require(all(row[key] == str(_csv_value(original[key])) for key in IMMUTABLE), 'REVIEW_INPUT_CHANGED', row['turn_id'])
        require(row['reviewer_id'] == task['reviewer'], 'REVIEWER_MISMATCH')
        require(row['bloom_level'] in ['1','2','3','4','5','6','NA'], 'INCOMPLETE_REVIEW')
        label = None if row['bloom_level']=='NA' else int(row['bloom_level'])
        require(row['insufficient_evidence'] in ['yes','no'] and row['outsourcing'] in ['yes','no','uncertain'], 'INVALID_REVIEW_FLAGS')
        require(row['confidence_1_to_5'] in ['1','2','3','4','5'], 'INVALID_CONFIDENCE')
        try: datetime.fromisoformat(row['reviewed_at'])
        except ValueError: require(False, 'INVALID_REVIEW_DATE')
        label_check(label, row['evidence_quote'], original['question'], row['insufficient_evidence']=='yes', row['notes'] or 'human rating')
        require(label is not None or row['notes'].strip(), 'NA_REASON_REQUIRED')
        result.append({'turn_id': row['turn_id'], 'label': label, 'evidence': row['evidence_quote'],
                       'outsourcing': row['outsourcing'], 'reason': row['notes'], 'reviewed_at': row['reviewed_at'],
                       'subjective_confidence': int(row['confidence_1_to_5'])})
    return store.put('review', {'task': task_id, 'dataset': task['dataset'], 'rubric': task['rubric'],
                               'pool': task['pool'], 'reviewer': task['reviewer'], 'rows': sorted(result,key=lambda x:x['turn_id']),
                               'imported_file_hash': file_hash(path)}, [task_id])


def adjudicate(store, review_a, review_b, resolutions, actor):
    for aid in [review_a,review_b]: store.verify_tree(aid)
    a, b = (store.get(x, 'review')['payload'] for x in [review_a, review_b])
    require(a['reviewer'] != b['reviewer'], 'INDEPENDENT_REVIEWERS_REQUIRED')
    require(isinstance(actor, str) and actor.strip() and actor not in [a['reviewer'], b['reviewer']], 'INDEPENDENT_ADJUDICATOR_REQUIRED')
    require(all(a[k] == b[k] for k in ['dataset','rubric','pool']), 'REVIEW_VERSION_MISMATCH')
    left, right = ({r['turn_id']:r for r in x['rows']} for x in [a,b])
    require(set(left)==set(right), 'REVIEW_SAMPLE_CHANGED')
    conflicts = {tid for tid in left if any(left[tid][k] != right[tid][k] for k in ['label','outsourcing'])}
    require(isinstance(resolutions, list), 'INVALID_RESOLUTIONS')
    fixed = {x['turn_id']:x for x in resolutions}
    require(len(fixed)==len(resolutions) and set(fixed)==conflicts, 'UNRESOLVED_OR_EXTRA_CONFLICTS')
    dataset = store.get(a['dataset'], 'dataset')['payload']
    turns = {x['turn_id']:x for x in dataset['turns']}
    rows = []
    for tid in sorted(left):
        row = dict(fixed[tid] if tid in fixed else left[tid])
        label_check(row['label'], row.get('evidence',''), turns[tid]['question'], row['label'] is None,
                    row.get('reason','') if tid in fixed else 'independent reviewers agree')
        require(row.get('outsourcing') in ['yes','no','uncertain'], 'INVALID_OUTSOURCING')
        row['origin'] = 'adjudicated' if tid in conflicts else 'human_agreement'
        rows.append(row)
    return store.put('gold', {'dataset': a['dataset'], 'rubric': a['rubric'], 'pool': a['pool'],
                             'reviewers': [a['reviewer'],b['reviewer']], 'adjudicator': actor,
                             'rows': rows, 'conflict_count': len(conflicts),
                             'independence_limit': 'Distinct IDs do not prove actual human independence.'}, [review_a, review_b])
