import json
from pathlib import Path
import tempfile
import unittest

from challenge.annotation.ai_reference import import_ai_reference, compare_ai_reference
from challenge.annotation.predictions import export_request, import_predictions
from challenge.annotation.reviews import register_rubric
from challenge.contracts import ContractError
from challenge.demo import synthetic_dataset
from challenge.evaluation import evaluate_quality
from challenge.storage import ArtifactStore


PROJECT = Path(__file__).resolve().parents[1]


class AIReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ArtifactStore(self.temp.name)
        dataset = synthetic_dataset(self.store)
        rubric = register_rubric(self.store, PROJECT/'docs/标注手册_v1.md', 'SYNTHETIC')
        task = export_request(self.store, dataset, rubric,
                              {'name':'test','revision':'1','parameters':{}},
                              PROJECT/'prompts/认知预标注_v1.md')
        self.task = task['task_id']
        source = self.store.get(self.task)['payload']['rows']
        self.rows = [dict(turn_id=r['turn_id'], label=1, evidence=r['question'],
                          reason='Synthetic reference', outsourcing='uncertain',
                          insufficient_evidence=False, self_reported_confidence=.8) for r in source]
        self.path = Path(self.temp.name)/'input.json'
        self.protocol = Path(self.temp.name)/'protocol.md'
        self.protocol.write_text('Synthetic AI review protocol; not human gold.', encoding='utf-8')
        self.batch = dict(task_id=self.task, reviewer='Codex AI', reviewed_at='2026-09-27',
                          exposure='context_exposed', rows=self.rows)

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding='utf-8')
        return self.path

    def reference(self):
        return import_ai_reference(self.store, self.task, self.write(self.batch), self.protocol)

    def predictions(self, rows):
        return import_predictions(self.store, self.task,
                                  self.write(dict(task_id=self.task, predictions=rows, usage=None)))

    def test_reference_is_not_gold_even_when_every_label_agrees(self):
        reference = self.reference()
        predictions = self.predictions(self.rows)
        report = self.store.get(compare_ai_reference(self.store, predictions, reference))['payload']
        self.assertFalse(report['quality_gate_passed'])
        self.assertEqual(report['six_level']['agreement_rate'], 1)
        self.assertNotIn('accuracy', report['six_level'])
        self.assertFalse(self.store.get(reference)['payload']['human_review'])
        with self.assertRaisesRegex(ContractError, 'ARTIFACT_TYPE_MISMATCH'):
            evaluate_quality(self.store, predictions, reference, {})

    def test_missingness_is_separate_from_six_and_three_level_agreement(self):
        self.rows[0].update(label=None, evidence='', insufficient_evidence=True)
        reference = self.reference()
        predictions = [dict(r) for r in self.rows]
        predictions[0].update(label=1, evidence='请比较两种方法', insufficient_evidence=False)
        predictions[1]['label'] = 2
        report = self.store.get(compare_ai_reference(self.store, self.predictions(predictions), reference))['payload']
        self.assertEqual(report['missingness']['reference_na_prediction_label'], 1)
        self.assertEqual(report['paired_n'], len(self.rows)-1)
        self.assertEqual(report['three_tier']['agreement_rate'], 1)
        self.assertLess(report['six_level']['agreement_rate'], 1)

    def test_invalid_or_duplicate_reference_rows_rejected(self):
        self.rows[0]['evidence'] = 'invented evidence'
        with self.assertRaisesRegex(ContractError, 'EVIDENCE_NOT_IN_QUESTION'): self.reference()
        self.rows[0] = dict(self.rows[1])
        with self.assertRaisesRegex(ContractError, 'ID_SET_MISMATCH'): self.reference()

    def test_protocol_and_exposure_are_required(self):
        self.batch['exposure'] = 'independent_human'
        with self.assertRaisesRegex(ContractError, 'INVALID_AI_EXPOSURE'): self.reference()
        self.batch['exposure'] = 'context_exposed'
        self.protocol.write_text(' ', encoding='utf-8')
        with self.assertRaisesRegex(ContractError, 'EMPTY_AI_PROTOCOL'): self.reference()

    def test_same_ids_from_different_task_cannot_compare(self):
        reference = self.reference()
        predictions = self.predictions(self.rows)
        payload = self.store.get(predictions)['payload']
        payload['task'] = 'different-task'
        other = self.store.put('predictions', payload, [predictions])
        with self.assertRaisesRegex(ContractError, 'AI_REFERENCE_TASK_MISMATCH'):
            compare_ai_reference(self.store, other, reference)

    def test_all_na_has_no_agreement_estimate(self):
        for row in self.rows: row.update(label=None, evidence='', insufficient_evidence=True)
        reference = self.reference()
        report = self.store.get(compare_ai_reference(self.store, self.predictions(self.rows), reference))['payload']
        self.assertEqual(report['paired_n'], 0)
        self.assertIsNone(report['six_level']['agreement_rate'])
        self.assertIsNone(report['three_tier']['agreement_rate'])
        self.assertEqual(report['missingness']['both_na'], len(self.rows))

    def test_failed_predictions_are_not_counted_as_na(self):
        reference = self.reference()
        task = self.store.get(self.task)['payload']
        partial = self.store.put('partial_predictions', dict(
            task=self.task, dataset=task['dataset'], rubric=task['rubric'], rows=self.rows[1:],
            failed_turn_ids=[self.rows[0]['turn_id']]), [self.task])
        report = self.store.get(compare_ai_reference(self.store, partial, reference))['payload']
        self.assertEqual(report['failed_predictions'], 1)
        self.assertEqual(report['paired_n'], len(self.rows)-1)
        self.assertEqual(report['missingness']['both_na'], 0)
        self.assertEqual(report['missingness']['reference_label_prediction_na'], 0)
        self.assertFalse(report['quality_gate_passed'])

    def test_partial_prediction_ids_must_exhaust_task_without_overlap(self):
        reference = self.reference()
        task = self.store.get(self.task)['payload']
        for failed in ([], [self.rows[1]['turn_id']]):
            partial = self.store.put('partial_predictions', dict(
                task=self.task,dataset=task['dataset'],rubric=task['rubric'],rows=self.rows[1:],
                failed_turn_ids=failed), [self.task])
            with self.assertRaisesRegex(ContractError, 'AI_REFERENCE_ID_SET_MISMATCH'):
                compare_ai_reference(self.store, partial, reference)

    def test_unknown_and_unattempted_snapshot_rows_are_separate(self):
        reference = self.reference()
        task = self.store.get(self.task)['payload']
        snapshot = self.store.put('prediction_snapshot',dict(
            task=self.task,dataset=task['dataset'],rubric=task['rubric'],rows=self.rows[2:],
            unavailable=[dict(turn_id=self.rows[0]['turn_id'],status='submitted_unknown'),
                         dict(turn_id=self.rows[1]['turn_id'],status='not_attempted')]),[self.task])
        report = self.store.get(compare_ai_reference(self.store,snapshot,reference))['payload']
        self.assertEqual(report['failed_predictions'],0)
        self.assertEqual(report['unknown_predictions'],1)
        self.assertEqual(report['unattempted_predictions'],1)
        self.assertEqual(report['paired_n'],len(self.rows)-2)
