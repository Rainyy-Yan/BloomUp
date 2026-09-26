import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from challenge.__main__ import main
from challenge.causal import import_panel, audit_panel, build_causal_report
from challenge.storage import ArtifactStore
from tests.test_causal import fixture


class CausalWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ArtifactStore(self.temp.name)
        self.document, self.protocol = fixture()

    def audit(self):
        panel = import_panel(self.store,self.document)
        return self.store.get(audit_panel(self.store,panel,self.protocol))['payload']

    def test_balance_tracks_actual_weighted_composition(self):
        diagnostics = self.audit()['diagnostics']
        baseline = next(r for r in diagnostics['balance'] if r['feature'] == 'pre_score')
        self.assertAlmostEqual(baseline['mean_treated'],50)
        self.assertAlmostEqual(baseline['mean_control'],40)
        self.assertAlmostEqual(baseline['weighted_control_mean'],50)
        self.assertGreater(baseline['smd_before'],0)
        self.assertAlmostEqual(baseline['smd_after'],0)
        self.assertAlmostEqual(diagnostics['weights']['control_ess'],24)
        self.assertAlmostEqual(diagnostics['weights']['max_control_cluster_share'],2/36)
        for row in diagnostics['balance']:
            self.assertAlmostEqual(row['smd_after'],0)
        self.assertNotIn('PRIVATE-',json.dumps(diagnostics))

    def test_baseline_imbalance_not_removed_by_unrelated_strata(self):
        for row in self.document['rows']:
            row['pre_score'] += 10*row['treated']
            row['post_score'] += 10*row['treated']
        diagnostics = self.audit()['diagnostics']
        baseline = next(r for r in diagnostics['balance'] if r['feature'] == 'pre_score')
        self.assertGreater(baseline['smd_after'],.1)
        self.assertIn('RESIDUAL_BASELINE_IMBALANCE',diagnostics['warnings'])

    def test_no_overlap_never_creates_clipped_weights(self):
        for row in self.document['rows']:
            row['covariates']['foundation']=str(row['treated'])
        audit = self.audit()
        self.assertIn('NO_COMMON_SUPPORT',audit['blockers'])
        self.assertIsNone(audit['diagnostics']['weights'])
        self.assertTrue(all(r['smd_after'] is None for r in audit['diagnostics']['balance']))

    def test_zero_variance_imbalance_is_not_reported_as_zero_smd(self):
        for row in self.document['rows']:
            row['pre_score'] = 20+20*row['treated']
        diagnostics = self.audit()['diagnostics']
        baseline = next(r for r in diagnostics['balance'] if r['feature'] == 'pre_score')
        self.assertIsNone(baseline['smd_before'])
        self.assertEqual(baseline['scale_status'],'zero_variance_imbalance')
        self.assertIn('RESIDUAL_BASELINE_IMBALANCE',diagnostics['warnings'])

    def test_workflow_success_and_blocked_results_are_traceable(self):
        from challenge.causal_workflow import run_causal_workflow
        out = run_causal_workflow(self.store,self.document,self.protocol,replicates=100)
        self.assertEqual(out['analysis_status'],'estimated_under_assumptions')
        self.store.verify_tree(out['run'])
        self.assertIsNotNone(out['effect'])
        text=Path(out['report']['markdown']).read_text(encoding='utf-8')
        self.assertIn('基线平衡',text)
        self.assertIn('权重',text)
        self.assertNotIn('PRIVATE-',text)
        self.protocol['outcome']['independent']=False
        blocked = run_causal_workflow(self.store,self.document,self.protocol,replicates=100)
        self.assertEqual(blocked['analysis_status'],'blocked')
        self.assertIsNone(blocked['effect'])
        self.store.verify_tree(blocked['run'])
        text=Path(blocked['report']['markdown']).read_text(encoding='utf-8')
        self.assertIn('NO_INDEPENDENT_OUTCOME',text)
        self.assertNotIn('独立终点：',text)

    def test_workflow_distinguishes_point_estimate_from_inference(self):
        from challenge.causal_workflow import run_causal_workflow
        for row in self.document['rows']: row['cluster_id']=str(row['treated'])
        out = run_causal_workflow(self.store,self.document,self.protocol,replicates=100)
        self.assertEqual(out['analysis_status'],'estimated_without_inference')
        self.assertEqual(out['inference_status'],'insufficient_clusters')

    def test_cli_run_blocked_still_writes_reviewable_report(self):
        self.protocol['frozen_by']=''
        root = Path(self.temp.name)
        (root/'panel.json').write_text(json.dumps(self.document),encoding='utf-8')
        (root/'protocol.json').write_text(json.dumps(self.protocol),encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            code=main(['--root',str(root),'causal-run','--file',str(root/'panel.json'),
                       '--protocol',str(root/'protocol.json'),'--replicates','100'])
        receipt=json.loads(stream.getvalue())
        self.assertEqual(code,2)
        self.assertEqual(receipt['status'],'blocked')
        self.assertTrue(Path(receipt['result']['report']['markdown']).is_file())

    def test_validation_report_uses_actual_artifact_and_exposes_violations(self):
        validation = self.store.put('causal_validation',dict(synthetic=True,seed=42,replicates=100,simulations=20,
            rows=[dict(scenario='violated_parallel_trends',true_effect=0,imposed_differential_trend=4,
                       simulations=20,inference_available=20,mean_estimate=4,bias=4,mean_unadjusted_did=6,
                       coverage_among_valid=0,coverage_mc_se=0,rejection_rate_among_valid=1)],
            limits=['Synthetic fixture only']))
        out = build_causal_report(self.store,validation)
        self.store.verify_tree(out['artifact_id'])
        text=Path(out['markdown']).read_text(encoding='utf-8')
        self.assertIn('violated_parallel_trends',text)
        self.assertIn(validation,text)
        self.assertIn('平行趋势',text)

    def test_missing_baseline_and_singleton_do_not_fabricate_balance(self):
        self.document['rows'][0]['pre_score']=None
        diagnostics=self.audit()['diagnostics']
        baseline=diagnostics['balance'][0]
        self.assertIsNone(baseline['smd_before'])
        self.assertIsNone(baseline['weighted_control_mean'])
        self.assertIn('BALANCE_INCOMPLETE',diagnostics['warnings'])
        self.document,self.protocol=fixture()
        self.document['rows']=[next(r for r in self.document['rows'] if r['treated']==d) for d in (0,1)]
        baseline=self.audit()['diagnostics']['balance'][0]
        self.assertIsNone(baseline['smd_after'])

    def test_concentrated_cluster_weights_are_visible(self):
        for row in self.document['rows']:
            row['cluster_id']=f"arm-{row['treated']}"
        diagnostics=self.audit()['diagnostics']
        self.assertAlmostEqual(diagnostics['weights']['control_cluster_ess'],1)
        self.assertAlmostEqual(diagnostics['weights']['max_control_cluster_share'],1)
        self.assertIn('CLUSTER_WEIGHT_CONCENTRATION',diagnostics['warnings'])

    def test_workflow_id_reproducible_and_invalid_config_fails_before_import(self):
        from challenge.causal_workflow import run_causal_workflow
        with self.assertRaisesRegex(ValueError,'INVALID_BOOTSTRAP_CONFIG'):
            run_causal_workflow(self.store,self.document,self.protocol,replicates=2)
        self.assertFalse((self.store.root/'artifacts').exists())
        left=run_causal_workflow(self.store,self.document,self.protocol,seed=14,replicates=100)
        right=run_causal_workflow(self.store,self.document,self.protocol,seed=14,replicates=100)
        self.assertEqual(left['run'],right['run'])
        self.assertNotEqual(left['report']['markdown'],right['report']['markdown'])


if __name__ == '__main__': unittest.main()
