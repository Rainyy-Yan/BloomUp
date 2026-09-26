"""Analysis label snapshots and conservative descriptive scoring."""

from collections import Counter, defaultdict

from .contracts import require
from .metrics import student_metrics
from .core import _simplex


def freeze_labels(store, admission_id, rubric_id, gold_ids, predictions_id=None, quality_id=None):
    dependencies = [admission_id, rubric_id, *gold_ids]
    if predictions_id: dependencies.append(predictions_id)
    if quality_id: dependencies.append(quality_id)
    for aid in dependencies: store.verify_tree(aid)
    admission = store.get(admission_id, 'admission')['payload']
    rubric = store.get(rubric_id, 'rubric')['payload']
    require(rubric['status']=='frozen', 'RUBRIC_NOT_FROZEN')
    dataset_id = admission['dataset']
    dataset = store.get(dataset_id, 'dataset')['payload']
    require(bool(predictions_id) == bool(quality_id), 'MODEL_QUALITY_REQUIRED')
    adopted = {}
    if predictions_id:
        pred = store.get(predictions_id, 'predictions')['payload']
        quality = store.get(quality_id, 'quality')['payload']
        require(pred['dataset']==dataset_id and pred['rubric']==rubric_id, 'LABEL_VERSION_MISMATCH')
        require(quality['predictions']==predictions_id and quality['passed'], 'MODEL_QUALITY_BLOCKED')
        adopted.update({x['turn_id']:dict(x,origin='model_quality_gated',source_artifact=predictions_id) for x in pred['rows']})
    human = {}
    for gold_id in gold_ids:
        gold = store.get(gold_id, 'gold')['payload']
        require(gold['dataset']==dataset_id and gold['rubric']==rubric_id, 'LABEL_VERSION_MISMATCH')
        for row in gold['rows']:
            tid = row['turn_id']
            require(tid not in human, 'OVERLAPPING_GOLD_SOURCES')
            human[tid] = dict(row,source_artifact=gold_id)
    adopted.update(human)
    accepted = set(admission['accepted_records'])
    turns = [x for x in dataset['turns'] if x['record_id'] in accepted]
    require(turns, 'NO_ADMITTED_TURNS')
    rows = []
    for turn in turns:
        value = adopted.get(turn['turn_id'])
        rows.append({'turn_id':turn['turn_id'], 'label':value['label'] if value else None,
                     'origin':value['origin'] if value else 'missing',
                     'source_artifact':value['source_artifact'] if value else None,
                     'missing_reason':None if value and value['label'] is not None else 'human_or_model_na' if value else 'not_labeled'})
    return store.put('labels', {'dataset':dataset_id, 'admission':admission_id, 'rubric':rubric_id,
                               'rows':rows, 'origins':dict(Counter(x['origin'] for x in rows)),
                               'scope_status':admission['scope_status'], 'analysis_status':'exploratory',
                               'claim_type':'descriptive', 'synthetic':dataset['synthetic']}, dependencies)


def compute_metrics(store, labels_id, spec):
    store.verify_tree(labels_id)
    require(set(spec)=={'reference_distribution','weights','reference_status'}, 'INVALID_METRIC_SPEC')
    _simplex(spec['reference_distribution'],6)
    _simplex(spec['weights'],3)
    require(spec['reference_status'] in ['provisional','confirmed'], 'INVALID_REFERENCE_STATUS')
    labels = store.get(labels_id, 'labels')['payload']
    dataset = store.get(labels['dataset'], 'dataset')['payload']
    index = {x['turn_id']:x['label'] for x in labels['rows']}
    turns = [x for x in dataset['turns'] if x['turn_id'] in index]
    rows = student_metrics(turns,index,spec['reference_distribution'],spec['weights'])
    present = {(x['term'],x['student_key']) for x in rows}
    for student in dataset['inventory']:
        if (student['term'],student['student_key']) not in present:
            rows.append({'term':student['term'],'student_key':student['student_key'], 'candidate_turns':0,
                         'labeled_turns':0,'coverage':0,'endpoint_pairs':0, 'abl':None,'hot':None,'ctq':None,'dhi':None,
                         'aiv':None,'rank':None,'mab':None,'observed_hot_change':None,'identification_bounds':None,
                         'sampling_ci':None,'label_sensitivity_interval':None,'missing_reasons':['no_admitted_turns'],
                         'mab_missing_reason':'no_admitted_turns','claim_type':'descriptive','analysis_status':'exploratory'})
    return store.put('metrics', {'dataset':labels['dataset'],'labels':labels_id,'spec':spec,
                                'rows':sorted(rows,key=lambda x:(x['term'],x['student_key'])),
                                'origins':labels['origins'],'synthetic':dataset['synthetic'],
                                'scope_status':labels['scope_status'],'analysis_status':'exploratory','claim_type':'descriptive',
                                'limits':['Scores describe observed question demands, not independently measured learning ability.',
                                          'AIV uses available labels and available original endpoint pairs; coverage may be incomplete.',
                                          'CTQ-missing bounds hold available-label HOT/DHI fixed; not full missing-label bounds or confidence intervals.']},
                     [labels_id], spec)


def sensitivity(store, metrics_id):
    store.verify_tree(metrics_id)
    metric = store.get(metrics_id,'metrics')['payload']
    base = metric['spec']['weights']
    scenarios = {'primary':base,'balanced':[1/3]*3}
    for i in range(3):
        for multiplier in [.9,1.1]:
            weights = [value*(multiplier if j==i else 1) for j,value in enumerate(base)]
            scenarios[f'w{i+1}_x{multiplier}'] = [w/sum(weights) for w in weights]
    eligible_by_term = defaultdict(list)
    for row in metric['rows']:
        if row['aiv'] is not None:
            eligible_by_term[row['term']].append(row)
    results = []
    for term, rows in sorted(eligible_by_term.items()):
        for name, weights in scenarios.items():
            values = [100*sum(w*r[k] for w,k in zip(weights,['hot','ctq','dhi'])) for r in rows]
            results.append({'term':term,'scenario':name,'weights':weights,'students':len(values),'mean_aiv':sum(values)/len(values)})
    return store.put('sensitivity', {'dataset':metric['dataset'],'metrics':metrics_id,'rows':results,
                                    'claim_type':'descriptive','limits':['Within-term weight sensitivity only; same available-score students in each term.',
                                                                      'Not label-error uncertainty or reference-distribution sensitivity.']},[metrics_id])
