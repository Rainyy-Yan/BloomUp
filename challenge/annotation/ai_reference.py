"""Explicit AI development references, isolated from human gold and quality gates."""

from datetime import date
from pathlib import Path

from ..contracts import read_json, require
from ..evaluation import agreement
from .predictions import validate_prediction


def import_ai_reference(store, task_id, path, protocol_path):
    store.verify_tree(task_id)
    task = store.get(task_id, 'prediction_task')['payload']
    batch = read_json(path)
    require(isinstance(batch, dict) and set(batch) ==
            {'task_id', 'reviewer', 'reviewed_at', 'exposure', 'rows'}, 'INVALID_AI_REFERENCE')
    require(batch['task_id'] == task_id, 'AI_REFERENCE_TASK_MISMATCH')
    require(isinstance(batch['reviewer'], str) and batch['reviewer'].strip(), 'AI_REVIEWER_REQUIRED')
    require(batch['exposure'] in ('context_exposed', 'predictions_exposed', 'not_recorded'),
            'INVALID_AI_EXPOSURE')
    require(isinstance(batch['reviewed_at'], str), 'INVALID_REVIEW_DATE')
    date.fromisoformat(batch['reviewed_at'])
    protocol = Path(protocol_path).read_text(encoding='utf-8-sig')
    require(protocol.strip(), 'EMPTY_AI_PROTOCOL')
    index = {row['turn_id']: row for row in task['rows']}
    rows = batch['rows']
    require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
            and len(rows) == len(index) and all(isinstance(row.get('turn_id'), str) for row in rows)
            and {row['turn_id'] for row in rows} == set(index), 'AI_REFERENCE_ID_SET_MISMATCH')
    for row in rows:
        validate_prediction(row, index[row['turn_id']])
        require(not row['evidence'] or row['evidence'] in index[row['turn_id']]['question'],
                'EVIDENCE_NOT_IN_QUESTION')
    return store.put('ai_reference', dict(
        task=task_id, dataset=task['dataset'], rubric=task['rubric'], pool=task['pool'],
        reviewer=batch['reviewer'], reviewed_at=batch['reviewed_at'], exposure=batch['exposure'],
        protocol=protocol, rows=sorted(rows, key=lambda row: row['turn_id']),
        human_review=False, independent_audit=False, confidence_status='self_reported_uncalibrated',
        purpose='development_diagnostic'), [task_id])


def _agreement_summary(pairs):
    stats = agreement(pairs)
    return dict(confusion=stats['confusion'], agreement_rate=stats['accuracy'],
                kappa=stats['kappa'], linear_kappa=stats['linear_kappa'])


def compare_ai_reference(store, predictions_id, reference_id):
    for aid in (predictions_id, reference_id): store.verify_tree(aid)
    prediction_artifact = store.get(predictions_id)
    require(prediction_artifact['artifact_type'] in ('predictions', 'partial_predictions', 'prediction_snapshot'),
            'ARTIFACT_TYPE_MISMATCH')
    predictions = prediction_artifact['payload']
    reference = store.get(reference_id, 'ai_reference')['payload']
    require(all(predictions[key] == reference[key] for key in ('task', 'dataset', 'rubric')),
            'AI_REFERENCE_TASK_MISMATCH')
    index = {row['turn_id']: row for row in predictions['rows']}
    failed_ids = predictions['failed_turn_ids'] if prediction_artifact['artifact_type'] == 'partial_predictions' else []
    unavailable = (predictions['unavailable'] if prediction_artifact['artifact_type'] == 'prediction_snapshot'
                   else [dict(turn_id=tid,status='validation_failed') for tid in failed_ids])
    require(isinstance(unavailable,list) and all(isinstance(row,dict) and set(row)=={'turn_id','status'}
            and row['status'] in ('validation_failed','submitted_unknown','usage_unverified','not_attempted')
            for row in unavailable), 'INVALID_UNAVAILABLE_PREDICTIONS')
    failed_ids = [row['turn_id'] for row in unavailable]
    require(isinstance(failed_ids, list) and all(isinstance(tid, str) for tid in failed_ids)
            and len(set(failed_ids)) == len(failed_ids) and not set(failed_ids).intersection(index)
            and len(index) == len(predictions['rows'])
            and set(index).union(failed_ids) == {row['turn_id'] for row in reference['rows']},
            'AI_REFERENCE_ID_SET_MISMATCH')
    missingness = dict(both_na=0, reference_na_prediction_label=0,
                       reference_label_prediction_na=0, both_labeled=0)
    pairs, disagreements = [], []
    for row in reference['rows']:
        if row['turn_id'] in failed_ids: continue
        left, right = row['label'], index[row['turn_id']]['label']
        if left is None and right is None: missingness['both_na'] += 1
        elif left is None: missingness['reference_na_prediction_label'] += 1
        elif right is None: missingness['reference_label_prediction_na'] += 1
        else:
            missingness['both_labeled'] += 1
            pairs.append((left, right, 1))
        if left != right:
            disagreements.append(dict(turn_id=row['turn_id'], ai_reference=left, prediction=right))
    tiers = [[0]*3 for _ in range(3)]
    for left, right, _ in pairs: tiers[(left-1)//2][(right-1)//2] += 1
    total = len(pairs)
    tier_rate = sum(tiers[i][i] for i in range(3))/total if total else None
    chance = sum(sum(tiers[i])*sum(row[i] for row in tiers) for i in range(3))/total**2 if total else None
    tier_kappa = (tier_rate-chance)/(1-chance) if chance is not None and chance < 1-1e-12 else None
    return store.put('ai_reference_comparison', dict(
        predictions=predictions_id, ai_reference=reference_id, task=predictions['task'],
        dataset=predictions['dataset'], rubric=predictions['rubric'],
        n=len(reference['rows']), paired_n=total,
        failed_predictions=sum(row['status']=='validation_failed' for row in unavailable),
        unknown_predictions=sum(row['status'] in ('submitted_unknown','usage_unverified') for row in unavailable),
        unattempted_predictions=sum(row['status']=='not_attempted' for row in unavailable), missingness=missingness,
        six_level=_agreement_summary(pairs),
        three_tier=dict(mapping=[[1,2],[3,4],[5,6]], confusion=tiers,
                        agreement_rate=tier_rate, kappa=tier_kappa),
        disagreements=disagreements, quality_gate_passed=False,
        human_review=False, independent_audit=False, sampling_ci=None,
        limits=['AI-reference agreement is not accuracy against human ground truth.',
                'Contract failures are counted separately, never converted into model NA labels.',
                'Both-label denominators exclude NA; missingness is reported separately.',
                'Development review may be context-exposed; no independent audit is asserted.',
                'Three-tier high (L5-L6) differs from HOT (L4-L6).',
                'Self-reported confidence is not a calibrated probability.']), [predictions_id, reference_id])
