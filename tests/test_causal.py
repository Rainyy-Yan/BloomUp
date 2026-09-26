import copy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from challenge.causal import import_panel, audit_panel, estimate_effect, build_causal_report
from challenge.storage import ArtifactStore
from challenge.__main__ import main


def fixture(effect=5):
    protocol = {
        'schema_version':'causal-protocol-v1', 'term':'synthetic',
        'treatment_contrast':'Specified AI support versus specified conventional teaching',
        'time_zero':'2025-10-01T00:00:00+00:00',
        'followup_window':['2025-11-01T00:00:00+00:00','2025-11-07T00:00:00+00:00'],
        'outcome':{'name':'Independent common test', 'lower':0,'upper':100,
                   'independent':True,'comparable':True,'higher_is_better':True},
        'covariates':['foundation'], 'frozen_by':'SYNTHETIC',
        'design_evidence':{k:'Synthetic fixture only' for k in ['assignment','comparison','outcome','covariates','population']},
        'assumptions':{k:{'status':'assumed','rationale':'Synthetic design assumption'}
                       for k in ['parallel_trends','no_anticipation','no_interference']},
        'min_clusters_per_arm':10, 'educational_threshold':3, 'trend_bias_bounds':[0,2,5],
    }
    rows=[]
    for d in (0,1):
        for group, count in [('low',24 if d == 0 else 12),('high',12 if d == 0 else 24)]:
            for j in range(count):
                pre=30 if group == 'low' else 60
                trend=2 if group == 'low' else 8
                noise=(-1 if j % 2 else 1)
                rows.append(dict(student_key=f'PRIVATE-{d}-{group}-{j}', cluster_id=f'cluster-{d}-{group}-{j}',
                                 term='synthetic',treated=d,pre_score=pre,post_score=pre+trend+d*effect+noise,
                                 baseline_time='2025-09-20T00:00:00+00:00',covariates_time='2025-09-20T00:00:00+00:00',
                                 treatment_time=protocol['time_zero'] if d else None,
                                 followup_time='2025-11-03T00:00:00+00:00',covariates={'foundation':group}))
    return {'schema_version':'causal-panel-v1','synthetic':True,'rows':rows},protocol


class CausalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=ArtifactStore(self.temp.name)
        self.document,self.protocol=fixture()

    def audit(self):
        return audit_panel(self.store,import_panel(self.store,self.document),self.protocol)

    def test_known_effect_corrects_composition_bias(self):
        aid=self.audit()
        self.assertEqual(self.store.get(aid)['payload']['status'],'ready_under_assumptions')
        eid=estimate_effect(self.store,aid,replicates=200)
        result=self.store.get(eid)['payload']
        self.assertAlmostEqual(result['estimate'],5)
        self.assertAlmostEqual(result['unadjusted_did'],7)
        self.assertLess(result['ci95'][0],5)
        self.assertGreater(result['ci95'][1],5)
        self.assertLess(result['p_value'],.05)
        self.assertEqual(len(result['heterogeneity']),2)
        for row in result['heterogeneity']:
            self.assertAlmostEqual(row['estimate'],5)
        self.assertEqual(result['sensitivity'][-1]['effect_bounds'],[0,10])
        self.store.verify_tree(eid)

    def test_missing_control_and_outcome_block_instead_of_complete_case(self):
        self.document['rows']=[r for r in self.document['rows'] if r['treated']]
        self.document['rows'][0]['post_score']=None
        aid=self.audit()
        audit=self.store.get(aid)['payload']
        self.assertIn('NO_VALID_CONTROL',audit['blockers'])
        self.assertIn('MISSING_OUTCOME_NO_STRATEGY',audit['blockers'])
        with self.assertRaisesRegex(ValueError,'CAUSAL_NOT_IDENTIFIED'):
            estimate_effect(self.store,aid,replicates=100)

    def test_time_leakage_and_unverified_assumptions_block(self):
        self.document['rows'][0]['covariates_time']='2025-10-02T00:00:00+00:00'
        self.protocol['assumptions']['parallel_trends']['status']='unverified'
        audit=self.store.get(self.audit())['payload']
        self.assertIn('POST_TREATMENT_COVARIATE',audit['blockers'])
        self.assertIn('ASSUMPTION_UNDOCUMENTED',audit['blockers'])

    def test_unsupported_strata_and_mixed_cluster_block(self):
        self.document['rows'][0]['covariates']['foundation']='only-control'
        self.document['rows'][-1]['cluster_id']=self.document['rows'][1]['cluster_id']
        audit=self.store.get(self.audit())['payload']
        self.assertIn('NO_COMMON_SUPPORT',audit['blockers'])
        self.assertIn('MIXED_TREATMENT_CLUSTER',audit['blockers'])

    def test_invalid_domain_and_duplicate_identity_rejected(self):
        self.document['rows'].append(copy.deepcopy(self.document['rows'][0]))
        with self.assertRaisesRegex(ValueError,'DUPLICATE_STUDENT'):
            import_panel(self.store,self.document)
        self.document,self.protocol=fixture()
        self.document['rows'][0]['treated']=True
        with self.assertRaises(ValueError): import_panel(self.store,self.document)

    def test_small_number_of_assignment_units_has_no_inference(self):
        for row in self.document['rows']:
            row['cluster_id']=f"one-class-{row['treated']}"
        aid=self.audit()
        result=self.store.get(estimate_effect(self.store,aid,replicates=100))['payload']
        self.assertAlmostEqual(result['estimate'],5)
        self.assertIsNone(result['ci95'])
        self.assertIsNone(result['p_value'])
        self.assertEqual(result['inference_status'],'insufficient_clusters')

    def test_zero_effect_reproducible_and_aggregate_only_report(self):
        self.document,self.protocol=fixture(0)
        aid=self.audit()
        first=estimate_effect(self.store,aid,seed=41,replicates=100)
        self.assertEqual(first,estimate_effect(self.store,aid,seed=41,replicates=100))
        result=self.store.get(first)['payload']
        self.assertAlmostEqual(result['estimate'],0)
        self.assertEqual(result['p_value'],1)
        report=build_causal_report(self.store,first)
        text=Path(report['markdown']).read_text(encoding='utf-8')
        self.assertIn('合成',text)
        self.assertIn('Independent common test',text)
        self.assertNotIn('PRIVATE-',text)
        self.assertNotIn('cluster-',text)
        self.assertNotIn('PRIVATE-',json.dumps(result))
        self.store.verify_tree(report['artifact_id'])

    def test_audit_report_preserves_specific_blockers(self):
        self.protocol['outcome']['independent']=False
        aid=self.audit()
        report=build_causal_report(self.store,aid)
        self.assertIn('NO_INDEPENDENT_OUTCOME',Path(report['markdown']).read_text(encoding='utf-8'))

    def test_cluster_duplication_does_not_invent_independent_observations(self):
        original=self.store.get(estimate_effect(self.store,self.audit(),replicates=100))['payload']
        duplicates=copy.deepcopy(self.document['rows'])
        for row in duplicates: row['student_key'] += '-second-member'
        self.document['rows'].extend(duplicates)
        doubled=self.store.get(estimate_effect(self.store,self.audit(),replicates=100))['payload']
        self.assertEqual(doubled['clusters'],original['clusters'])
        for left,right in zip(original['ci95'],doubled['ci95']): self.assertAlmostEqual(left,right)

    def test_lost_bootstrap_support_suppresses_interval(self):
        for d in (0,1):
            arm=[r for r in self.document['rows'] if r['treated']==d]
            for row in arm: row['covariates']['foundation']='common'
            arm[0]['covariates']['foundation']='rare'
        result=self.store.get(estimate_effect(self.store,self.audit(),replicates=100))['payload']
        self.assertEqual(result['inference_status'],'bootstrap_support_failure')
        self.assertLess(result['valid_replicates'],100)
        self.assertIsNone(result['ci95'])
        self.assertIsNone(result['p_value'])

    def test_false_parallel_trend_assumption_is_not_detected_by_schema(self):
        self.document,self.protocol=fixture(0)
        for row in self.document['rows']:
            row['post_score'] += 4*row['treated']
        audit=self.audit()
        self.assertEqual(self.store.get(audit)['payload']['status'],'ready_under_assumptions')
        result=self.store.get(estimate_effect(self.store,audit,replicates=100))['payload']
        self.assertAlmostEqual(result['estimate'],4)
        # True treatment effect was zero: the design cannot separate differential natural trends.
        self.assertEqual(result['claim_type'],'synthetic_validation')

    def test_unfrozen_template_and_bad_time_order_block(self):
        self.protocol['frozen_by']=''
        self.protocol['design_evidence']['comparison']=''
        self.document['rows'][0]['baseline_time']=self.protocol['time_zero']
        self.document['rows'][0]['treatment_time']=self.protocol['time_zero']
        audit=self.store.get(self.audit())['payload']
        self.assertTrue({'PROTOCOL_NOT_FROZEN','DESIGN_EVIDENCE_MISSING','BASELINE_NOT_PRETREATMENT',
                         'CONTROL_CONTAMINATION'}.issubset(audit['blockers']))

    def test_cli_causal_demo_and_legacy_guard(self):
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(main(['--root',self.temp.name,'causal-demo']),0)
        receipt=json.loads(stream.getvalue())
        self.assertEqual(len(receipt['result']['studies']),2)
        for study in receipt['result']['studies']:
            effect=self.store.get(study['effect'])['payload']
            self.assertAlmostEqual(effect['estimate'],study['known_effect'])
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(main(['--root',self.temp.name,'analysis-causal']),2)
        self.assertEqual(json.loads(stream.getvalue())['status'],'blocked')

    def test_unsupported_replications_and_degenerate_interval(self):
        aid=self.audit()
        with self.assertRaises(ValueError): estimate_effect(self.store,aid,replicates=99)
        for row in self.document['rows']:
            row['post_score']=row['pre_score']+3+5*row['treated']
        result=self.store.get(estimate_effect(self.store,self.audit(),replicates=100))['payload']
        self.assertEqual(result['inference_status'],'degenerate_bootstrap')
        self.assertIsNone(result['ci95'])


if __name__=='__main__': unittest.main()
