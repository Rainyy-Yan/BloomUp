"""Synthetic tests for opt-in private exports, with common-cohort ranks."""

import csv
from pathlib import Path
import tempfile
import unittest

from challenge.contracts import ContractError
from challenge.demo import run_demo
from challenge.storage import ArtifactStore
from challenge.student_export import build_rows, export_students
from tests.test_scoring import spec_v2


def row(student, term='fall', **changes):
    value = dict(student_key=student, term=term, hot=.5, ctq=.5, dhi=.5, mab=.5,
                 candidate_turns=2, labeled_turns=2, missing_reasons=[])
    value.update(changes)
    return value


class ExportTests(unittest.TestCase):
    def payload(self, rows):
        return dict(rows=rows, spec=spec_v2(), formula_version='aiv-v2', dataset='synthetic-dataset',
                    labels='synthetic-labels', synthetic=True, scope_status='provisional',
                    population_counts={'fall':4,'spring':1})

    def test_terms_and_complete_cohort_fixed_across_schemes(self):
        payload = self.payload([row('a'), row('b', hot=1), row('missing', ctq=None,
                                      missing_reasons=['no_valid_original_endpoints']), row('spring','spring',hot=0)])
        rows = build_rows(payload, 'synthetic-metrics')
        self.assertEqual(len(rows), 16)
        for r in rows:
            self.assertEqual(r['comparison_population'], 2 if r['term']=='fall' else 1)
            if r['anonymous_id']=='missing':
                self.assertIsNone(r['rank'])
                self.assertIsNone(r['aiv'])
                self.assertIn('no_valid_original_endpoints', r['missing_reasons'])
            if r['term']=='spring': self.assertEqual(r['rank'], 1)

    def test_ties_average_ranks_and_top_quartile_includes_boundary_ties(self):
        rows = build_rows(self.payload([row('a',hot=1),row('b',hot=1),row('c',hot=0),row('d',hot=0)]),'m')
        baseline = [r for r in rows if r['scenario']=='primary']
        self.assertEqual([r['rank'] for r in baseline], [1.5,1.5,3.5,3.5])
        self.assertEqual([r['top_quartile'] for r in baseline], [True,True,False,False])

    def test_zero_weight_missing_indicator_still_excludes_ranking(self):
        payload = self.payload([row('a',mab=None)])
        payload['spec']['weights'] = [.5,.3,.2,0]
        primary = next(r for r in build_rows(payload,'m') if r['scenario']=='primary')
        self.assertIsNotNone(primary['aiv'])
        self.assertIsNone(primary['rank'])
        self.assertEqual(primary['comparison_population'], 0)

    def test_legacy_rejected_and_duplicate_students_rejected(self):
        payload = self.payload([row('a')])
        payload['formula_version'] = 'legacy-v1'
        with self.assertRaisesRegex(ContractError, 'V2_EXPORT_REQUIRED'): build_rows(payload,'m')
        with self.assertRaisesRegex(ContractError, 'DUPLICATE_STUDENT'):
            build_rows(self.payload([row('a'),row('a')]),'m')

    def test_explicit_opt_in_and_local_export_provenance(self):
        with tempfile.TemporaryDirectory() as root:
            result = run_demo(root,'aiv-v2')
            store = ArtifactStore(root)
            with self.assertRaisesRegex(ContractError, 'STUDENT_EXPORT_NOT_ENABLED'):
                export_students(store,result['metrics'])
            exported = export_students(store,result['metrics'], enabled=True)
            path = Path(exported['file'])
            self.assertTrue(path.is_relative_to(store.root/'exports'))
            with path.open(encoding='utf-8-sig',newline='') as stream: rows=list(csv.DictReader(stream))
            self.assertEqual(len(rows), 28)
            self.assertTrue(all(r['dataset_id']==result['dataset'] for r in rows))
            self.assertTrue(all(r['rank']=='' for r in rows if r['anonymous_id']=='SYNTHETIC-6'))
            self.assertNotIn('question', rows[0])
            store.verify_tree(exported['artifact_id'])


if __name__=='__main__': unittest.main()
