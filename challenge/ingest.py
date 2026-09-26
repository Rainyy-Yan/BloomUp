"""Import immutable candidate data and explicit parsing admission evidence."""

import csv
import json
from pathlib import Path

from .contracts import require, read_json, file_hash, turn_hash


def register_run(store, run_path):
    run = Path(run_path).resolve()
    manifest = read_json(run/'generated_manifest.json')
    hashes = {x['path']: x['sha256'] for x in manifest}
    needed = ['private/turns.jsonl', 'private/records.jsonl', 'private/sampling_design.json',
              'config_snapshot.json', 'source_manifest.csv', 'readiness.json', 'annotations/parse_review.csv']
    for name in needed:
        require(name in hashes and file_hash(run/name) == hashes[name], 'PREPARED_INPUT_CHANGED', name)
    def lines(name):
        return [json.loads(line) for line in (run/name).read_text(encoding='utf-8').splitlines()]
    turns, records = lines('private/turns.jsonl'), lines('private/records.jsonl')
    require(len({x['turn_id'] for x in turns}) == len(turns), 'DUPLICATE_TURN')
    record_index = {x['record_id']: x for x in records}
    require(len(record_index) == len(records), 'DUPLICATE_RECORD')
    for row in turns:
        require(row['record_id'] in record_index and row.get('label') is None, 'INVALID_CANDIDATE_TURN')
        row['text_hash'] = turn_hash(row)
    def read_csv(name):
        with (run/name).open(encoding='utf-8-sig', newline='') as stream:
            return list(csv.DictReader(stream))
    config = read_json(run/'config_snapshot.json')
    readiness = read_json(run/'readiness.json')
    # Older prepared runs already contain these aggregate counts; no CSV identities are needed.
    population = readiness.get('population_counts')
    if population is None:
        population = {'fall': readiness['fall']['unique_ids'], 'spring': readiness['spring_roster_size']}
    require(isinstance(population, dict) and set(population) == {'fall', 'spring'}
            and all(type(n) is int and n >= 0 for n in population.values()), 'INVALID_POPULATION_COUNTS')
    inventory = [{'term': term, 'student_key': student}
                 for term, student in sorted({(r['term'], r['student_key']) for r in records})]
    require(all(r['term'] in population for r in inventory), 'INVALID_POPULATION_TERM')
    require(all(sum(r['term'] == term for r in inventory) <= n for term, n in population.items()),
            'POPULATION_COUNT_BELOW_RECORDS')
    payload = {'turns': turns, 'records': [{k:v for k,v in x.items() if k != 'raw_text'} for x in records],
               'sampling': read_json(run/'private/sampling_design.json'),
               'inventory': inventory, 'population_counts': population,
               'source_manifest': read_csv('source_manifest.csv'), 'preparation_run': run.name,
               'required_parse_sample': [x['record_id'] for x in read_csv('annotations/parse_review.csv')],
               'scope_status': 'provisional', 'synthetic': False,
               'scope_config': {k: config[k] for k in ['spring_start_inclusive', 'spring_end_exclusive', 'source_channel']}}
    return store.put('dataset', payload, config=payload['scope_config'])


def admit_dataset(store, dataset_id, evidence):
    store.verify_tree(dataset_id)
    dataset = store.get(dataset_id, 'dataset')['payload']
    require(isinstance(evidence.get('reviewer_id'), str) and evidence['reviewer_id'].strip(), 'REVIEWER_REQUIRED')
    require(evidence.get('dataset_id') == dataset_id, 'DATASET_MISMATCH')
    decisions = evidence.get('record_decisions', [])
    records = {x['record_id']: x for x in dataset['records']}
    ids = [x['record_id'] for x in decisions]
    require(len(ids) == len(set(ids)) and set(ids).issubset(records), 'INVALID_PARSE_REVIEW_IDS')
    accepted = set()
    for row in decisions:
        require(row['status'] in ['accepted', 'rejected'], 'INVALID_PARSE_DECISION')
        require(isinstance(row.get('reason'), str) and row['reason'].strip(), 'PARSE_REASON_REQUIRED')
        if row['status'] == 'accepted':
            require(records[row['record_id']]['parse_status'] == 'format_ok_pending_review', 'REPARSE_REQUIRED')
            accepted.add(row['record_id'])
    sample_policy = evidence.get('accept_remaining_format_ok', False)
    require(type(sample_policy) is bool, 'INVALID_PARSE_POLICY')
    if sample_policy:
        require(set(dataset['required_parse_sample']).issubset(ids), 'PARSE_SAMPLE_INCOMPLETE')
        require(evidence.get('policy_reason', '').strip(), 'PARSE_POLICY_REASON_REQUIRED')
        # Any sampled formatting-success record rejected defeats this generalization.
        require(not any(x['status']=='rejected' and records[x['record_id']]['parse_status']=='format_ok_pending_review'
                        for x in decisions), 'PARSE_SAMPLE_FAILURE')
        accepted.update(x['record_id'] for x in dataset['records'] if x['parse_status']=='format_ok_pending_review')
    require(accepted, 'NO_ADMITTED_RECORDS')
    scope = evidence.get('scope_status', 'provisional')
    require(scope in ['provisional', 'confirmed'], 'INVALID_SCOPE_STATUS')
    require(scope != 'confirmed' or evidence.get('scope_evidence', '').strip(), 'SCOPE_EVIDENCE_REQUIRED')
    return store.put('admission', {'dataset': dataset_id, 'accepted_records': sorted(accepted),
                                  'evidence': evidence, 'scope_status': scope,
                                  'semantic_status': 'sampled_accepted' if sample_policy else 'individually_accepted'}, [dataset_id])
