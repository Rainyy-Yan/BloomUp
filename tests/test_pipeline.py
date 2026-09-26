import csv
import json
from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook

from challenge.pipeline import ROOT, _csv_value, prepare, validate_annotation
from challenge.ingest import register_run
from challenge.storage import ArtifactStore


def save_book(path, sheets):
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
    workbook.save(path)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)/'project'
        source = Path(self.temp.name)/'source'
        self.project.mkdir()
        source.mkdir()
        fall_header = ['学号', '问答记录', '问题建立时间', '问答来源', '智能体类型']
        save_book(source/'fall.xlsx', {
            '问答记录': [fall_header, ['001', 'Q:概念\nA:答\nQ:应用\nA:答', '2025-11-01 00:00:00', 'AI智能体', '探知侠']],
            '智能体使用次数': [['学号', '思政点灯人', '探知侠', '数模全才', '数模匹配', '合计'], ['001', 0, 1, 0, 0, 1]],
        })
        save_book(source/'spring.xlsx', {'问答记录': [fall_header[:-1],
            ['002', 'Q:问题\nA:答', '2026-03-01 00:00:00', 'AI智能体'],
            ['002', 'Q:排除\nA:答', '2026-07-01 00:00:00', 'AI智能体'],
            ['003', 'Q:名单外\nA:答', '2026-03-02 00:00:00', 'AI智能体'],
            ['002', 'Q:其他渠道\nA:答', '2026-03-02 00:00:00', 'AI学伴']]})
        save_book(source/'roster.xlsx', {'学生参与情况': [['标题'], ['序号', '学号'], ['说明'], [1, '002'], [2, '004']]})
        self.config = json.loads((ROOT/'configs/preparation.json').read_text(encoding='utf-8'))
        self.config.update(source_root=str(source), fall_file='fall.xlsx', spring_file='spring.xlsx', roster_file='roster.xlsx')
        self.config_path = self.project/'config.json'
        self.config_path.write_text(json.dumps(self.config), encoding='utf-8')
        self.source = source

    def run_prepare(self):
        return prepare(self.project, self.config_path)

    def test_window_roster_channel_and_no_invented_scores(self):
        result = self.run_prepare()
        self.assertEqual(result['selected_records'], 2)
        self.assertEqual(result['candidate_turns'], 3)
        self.assertEqual(result['spring_exclusion_first_reason'], {
            'outside_provisional_window': 1, 'not_in_primary_roster': 1, 'different_channel': 1})
        self.assertFalse(result['formal_scores_computed'])
        run = self.project/'runs'/result['run_id']
        self.assertFalse((run/'student_inventory_NOT_SCORED.csv').exists())
        self.assertEqual(result['selected_students_by_term'], {'fall': 1, 'spring': 1})
        with (run/'annotations/development_rater_A.csv').open(encoding='utf-8-sig', newline='') as stream:
            self.assertNotIn('student_key', csv.DictReader(stream).fieldnames)
        for path in run.rglob('*.csv'):
            with path.open(encoding='utf-8-sig', newline='') as stream:
                self.assertNotIn('student_key', csv.DictReader(stream).fieldnames, str(path))
        text = (run/'private/turns.jsonl').read_text(encoding='utf-8')
        self.assertNotIn('"student_key": "001"', text)

    def test_import_rebuilds_only_candidate_student_inventory_in_memory(self):
        result = self.run_prepare()
        run = self.project/'runs'/result['run_id']
        store = ArtifactStore(self.project/'state')
        dataset = store.get(register_run(store,run))['payload']
        self.assertEqual(len(dataset['inventory']),2)
        self.assertEqual({row['term'] for row in dataset['inventory']},{'fall','spring'})
        self.assertFalse((run/'student_inventory_NOT_SCORED.csv').exists())

    def test_rerun_preserves_work_and_ids_and_source_change_stops(self):
        first = self.run_prepare()
        run = self.project/'runs'/first['run_id']
        annotated = run/'annotations/development_rater_A.csv'
        annotated.write_text('human work stays here', encoding='utf-8')
        second = self.run_prepare()
        run2 = self.project/'runs'/second['run_id']
        self.assertEqual(annotated.read_text(encoding='utf-8'), 'human work stays here')
        self.assertEqual((run/'private/turns.jsonl').read_bytes(), (run2/'private/turns.jsonl').read_bytes())
        (self.source/'new.md').write_text('new source version', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Source files changed'):
            self.run_prepare()

    def test_remote_inference_cannot_be_enabled_by_config(self):
        self.config['model']['remote_calls_enabled'] = True
        self.config_path.write_text(json.dumps(self.config), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'network inference'):
            self.run_prepare()

    def test_deleted_identity_key_cannot_silently_change_students(self):
        self.run_prepare()
        (self.project/'.local/pseudonym.key').unlink()
        with self.assertRaisesRegex(ValueError, 'identity key'):
            self.run_prepare()

    def test_csv_formula_text_is_display_safe(self):
        self.assertEqual(_csv_value('=1+2'), "'=1+2")
        self.assertEqual(_csv_value('  @SUM(A1:A2)'), "'  @SUM(A1:A2)")
        self.assertEqual(_csv_value(-2), -2)

    def test_blank_annotation_fails_instead_of_creating_gold(self):
        result = self.run_prepare()
        run = self.project/'runs'/result['run_id']
        index = run/'private/turns.jsonl'
        turns = [json.loads(x) for x in index.read_text(encoding='utf-8').splitlines()]
        sample = run/'blank.csv'
        sample.write_text('turn_id,bloom_level\n'+turns[0]['turn_id']+',\n', encoding='utf-8')
        checked = validate_annotation(sample, index)
        self.assertFalse(checked['valid'])
        self.assertEqual(checked['completed'], 0)

    def test_completed_annotation_and_sample_coverage(self):
        result = self.run_prepare()
        run = self.project/'runs'/result['run_id']
        index = run/'private/turns.jsonl'
        item = json.loads(index.read_text(encoding='utf-8').splitlines()[0])
        sample = run/'complete.csv'
        row = {'turn_id': item['turn_id'], 'bloom_level': '2', 'evidence_quote': item['question'],
               'outsourcing': 'no', 'insufficient_evidence': 'no', 'confidence_1_to_5': '3',
               'reviewer_id': 'test-rater', 'reviewed_at': '2026-09-25'}
        with sample.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
        self.assertTrue(validate_annotation(sample, index, [item['turn_id']])['valid'])
        self.assertFalse(validate_annotation(sample, index, [item['turn_id'], 'another'])['valid'])

    def test_truncated_csv_row_is_reported_as_incomplete(self):
        result = self.run_prepare()
        run = self.project/'runs'/result['run_id']
        index = run/'private/turns.jsonl'
        item = json.loads(index.read_text(encoding='utf-8').splitlines()[0])
        sample = run/'truncated.csv'
        sample.write_text('turn_id,bloom_level\n'+item['turn_id']+'\n', encoding='utf-8')
        self.assertFalse(validate_annotation(sample, index)['valid'])


if __name__ == '__main__':
    unittest.main()
