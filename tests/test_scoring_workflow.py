import json
from pathlib import Path
import tempfile
import unittest

from challenge.metrics import calculate_students
from challenge.demo import run_demo
from challenge.storage import ArtifactStore
from challenge.analysis import compute_metrics
from challenge.uncertainty import label_sensitivity
from tests.test_scoring import spec_v2


class ScoringWorkflow(unittest.TestCase):
    def setUp(self):
        self.spec = spec_v2()
        self.turns = [dict(term='synthetic', student_key='s', record_id='r', turn_index=i,
                           turn_id=str(i), agent_id='a') for i in (1, 2)]
        self.labels = {'1': 1, '2': 6}

    def test_dispatch_preserves_legacy_and_normalizes_v2(self):
        legacy = {k: self.spec[k] for k in ('reference_distribution', 'reference_status')}
        legacy['weights'] = [.5, .3, .2]
        old = calculate_students(self.turns, self.labels, legacy)[0]
        row = calculate_students(self.turns, self.labels, self.spec)[0]
        self.assertAlmostEqual(old['dhi'], .15)
        self.assertAlmostEqual(row['dhi'], 1-.85/.95)
        self.assertEqual(row['mab'], .5)
        self.assertAlmostEqual(row['aiv'], 25+30+15*(1-.85/.95)+2.5)
        self.assertAlmostEqual(sum(row['aiv_contributions'].values()), row['aiv'])

    def test_catalog_missing_and_outside_catalog_never_silently_score(self):
        self.spec.update(agent_catalog=None, agent_catalog_status='unknown')
        row = calculate_students(self.turns, self.labels, self.spec)[0]
        self.assertIsNone(row['aiv'])
        self.assertAlmostEqual(row['identification_bounds'][1]-row['identification_bounds'][0], 5)
        self.spec = spec_v2()
        self.turns[0]['agent_id'] = 'outside'
        with self.assertRaisesRegex(ValueError, 'OUTSIDE_FROZEN_CATALOG'):
            calculate_students(self.turns, self.labels, self.spec)

    def test_missing_original_endpoints_and_all_labels(self):
        row = calculate_students(self.turns, {'1': None, '2': 6}, self.spec)[0]
        self.assertIsNone(row['ctq'])
        self.assertAlmostEqual(row['identification_bounds'][1]-row['identification_bounds'][0], 30)
        row = calculate_students(self.turns, {'1': None, '2': None}, self.spec)[0]
        self.assertIsNone(row['aiv'])
        self.assertEqual(row['identification_bounds'], [2.5, 97.5])

    def test_custom_utilities_and_asymmetric_mode(self):
        self.spec.update(utilities=[0,.1,.3,.5,.9,1], dhi_mode='asymmetric', shortage_penalties=[1,1,1,2,3,4])
        row = calculate_students(self.turns, {'1': 2, '2': 5}, self.spec)[0]
        self.assertAlmostEqual(row['ctq'], .9)
        self.assertEqual(row['dhi'], row['dhi_asymmetric'])
        self.assertNotEqual(row['dhi'], row['dhi_symmetric'])

    def test_three_schemes_common_cohort_ties_and_weight_bounds(self):
        from challenge.comparison import compare_schemes, spearman
        rows = [dict(term='t', hot=x, ctq=y, dhi=.5, mab=.5) for x,y in [(1,0),(0,1),(.5,.5)]]
        rows.append(dict(term='t', hot=.9, ctq=None, dhi=.5, mab=.5))
        out = compare_schemes(rows, self.spec)
        self.assertEqual(len(out['rows']), 12)
        self.assertTrue(all(r['students'] == 3 and r['excluded_students'] == 1 for r in out['rows']))
        self.assertEqual({r['aggregation']['kind'] for r in out['rows']}, {'linear', 'concave'})
        for row in out['rows']:
            if row['weight_change_bound'] is not None:
                self.assertLessEqual(row['max_absolute_score_change'], row['weight_change_bound']+1e-9)
        self.assertAlmostEqual(spearman([1,1,2], [2,2,1]), -1)
        self.assertIsNone(spearman([1,1], [2,3]))
        self.assertNotIn('student_key', json.dumps(out))

    def test_v2_demo_report_noise_and_dependency_tree(self):
        with tempfile.TemporaryDirectory() as root:
            result = run_demo(root, formula_version='aiv-v2')
            store = ArtifactStore(root)
            store.verify_tree(result['report']['artifact_id'])
            metric = store.get(result['metrics'])['payload']
            self.assertEqual(metric['formula_version'], 'aiv-v2')
            self.assertEqual(metric['spec']['agent_catalog'], ['synthetic_agent'])
            noise = store.get(result['label_sensitivity'])['payload']
            baseline = next(r for r in noise['rows'] if r['error_mass'] == 0)
            actual = [r['aiv'] for r in metric['rows'] if r['aiv'] is not None]
            self.assertAlmostEqual(baseline['metrics']['aiv']['observed'], sum(actual)/len(actual))
            report = Path(result['report']['markdown']).read_text(encoding='utf-8')
            for text in ('aiv-v2', 'diminishing_returns', '条件上下界', 'Spearman'):
                self.assertIn(text, report)
            self.assertNotIn('SYNTHETIC-0', report)

    def test_empty_cohort_and_terms_are_not_pooled(self):
        from challenge.comparison import compare_schemes
        self.spec['weights'] = [1,0,0,0]
        rows = [dict(term='empty', hot=1, ctq=None, dhi=.5, mab=.5),
                dict(term='complete', hot=0, ctq=1, dhi=.5, mab=.5)]
        out = compare_schemes(rows, self.spec)
        self.assertEqual(len(out['rows']), 24)
        for row in out['rows']:
            if row['term'] == 'empty':
                self.assertEqual(row['students'], 0)
                self.assertIsNone(row['mean_aiv'])
            else:
                self.assertEqual(row['students'], 1)
            self.assertIsNone(row['rank_correlation_with_primary'])

    def test_concave_asymmetric_spec_survives_noise_recomputation(self):
        with tempfile.TemporaryDirectory() as root:
            result = run_demo(root, formula_version='aiv-v2')
            store = ArtifactStore(root)
            spec = store.get(result['metrics'])['payload']['spec']
            spec.update(aggregation={'kind':'concave','rho':2}, dhi_mode='asymmetric',
                        shortage_penalties=[1,1,1,2,3,4], utilities=[0,.1,.3,.5,.9,1])
            metric_id = compute_metrics(store, result['labels'], spec)
            noise_id = label_sensitivity(store, metric_id, error_masses=[0], replicates=100)
            observed = store.get(noise_id)['payload']['rows'][0]['metrics']['aiv']
            scores = [r['aiv'] for r in store.get(metric_id)['payload']['rows'] if r['aiv'] is not None]
            mean = sum(scores)/len(scores)
            self.assertAlmostEqual(observed['observed'], mean)
            for endpoint in observed['perturbation_interval_95']:
                self.assertAlmostEqual(endpoint, mean)

    def test_unknown_agent_identity_propagates_but_zero_weight_can_ignore_it(self):
        self.turns[0]['agent_id'] = None
        row = calculate_students(self.turns, self.labels, self.spec)[0]
        self.assertEqual(row['mab_missing_reason'], 'unknown_agent_identity')
        self.assertIsNone(row['aiv'])
        self.spec['weights'] = [.5,.3,.2,0]
        row = calculate_students(self.turns, self.labels, self.spec)[0]
        self.assertIsNotNone(row['aiv'])
        self.assertIsNone(row['identification_bounds'])


if __name__ == '__main__':
    unittest.main()
