import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from challenge.__main__ import main
from challenge.storage import ArtifactStore
from challenge.contracts import ContractError, read_json, label_check
from challenge.demo import synthetic_dataset, fill_synthetic_review, run_demo
from challenge.annotation.reviews import register_rubric, create_task, import_review, adjudicate, IMMUTABLE, EDITABLE
from challenge.annotation.predictions import export_request, import_predictions
from challenge.ingest import admit_dataset
from challenge.analysis import freeze_labels, compute_metrics, sensitivity
from challenge.evaluation import agreement, evaluate_quality
from challenge.pipeline import write_json, write_csv


PROJECT=Path(__file__).resolve().parents[1]


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=ArtifactStore(self.temp.name)
        self.dataset=synthetic_dataset(self.store)
        self.rubric=register_rubric(self.store,PROJECT/'docs/标注手册_v1.md','SYNTHETIC')

    def task(self, reviewer='A', levels=None):
        task=create_task(self.store,self.dataset,self.rubric,'audit',reviewer)
        fill_synthetic_review(task['file'],levels or [1,4]*6)
        return task

    def test_draft_rubric_cannot_enter_audit(self):
        draft=register_rubric(self.store,PROJECT/'docs/标注手册_v1.md')
        with self.assertRaisesRegex(ContractError,'RUBRIC_NOT_FROZEN'):
            create_task(self.store,self.dataset,draft,'audit','A')

    def test_edited_question_rejected(self):
        task=self.task()
        path=Path(task['file'])
        path.write_text(path.read_text(encoding='utf-8-sig').replace('合成问题','被改问题',1),encoding='utf-8-sig')
        with self.assertRaisesRegex(ContractError,'REVIEW_INPUT_CHANGED'): import_review(self.store,task['task_id'],path)

    def test_duplicate_row_rejected(self):
        task=self.task()
        with Path(task['file']).open(encoding='utf-8-sig',newline='') as stream: rows=list(csv.DictReader(stream))
        rows[-1]=rows[0]
        write_csv(Path(task['file']),rows,IMMUTABLE+EDITABLE)
        with self.assertRaisesRegex(ContractError,'REVIEW_SAMPLE_CHANGED'): import_review(self.store,task['task_id'],task['file'])

    def test_conflict_requires_third_person_and_explicit_resolution(self):
        a,b=self.task('A'),self.task('B',[2,4]+[1,4]*5)
        reviews=[import_review(self.store,x['task_id'],x['file']) for x in [a,b]]
        with self.assertRaisesRegex(ContractError,'UNRESOLVED'): adjudicate(self.store,*reviews,[],'C')
        with self.assertRaisesRegex(ContractError,'INDEPENDENT_ADJUDICATOR'): adjudicate(self.store,*reviews,[],'A')
        gold=adjudicate(self.store,*reviews,[{'turn_id':'demo-0-1','label':2,'evidence':'请比较两种方法','reason':'测试裁决','outsourcing':'no'}],'C')
        self.assertEqual(self.store.get(gold)['payload']['conflict_count'],1)

    def test_same_reviewer_not_double_blind(self):
        task=self.task()
        review=import_review(self.store,task['task_id'],task['file'])
        with self.assertRaisesRegex(ContractError,'INDEPENDENT_REVIEWERS'): adjudicate(self.store,review,review,[],'C')

    def test_changed_dataset_does_not_reuse_review(self):
        task=self.task()
        data=self.store.get(self.dataset)['payload']
        data['turns'][0]['question']='另一个问题'
        changed=self.store.put('dataset',data)
        other=create_task(self.store,changed,self.rubric,'audit','A')
        with self.assertRaisesRegex(ContractError,'REVIEW_INPUT_CHANGED'): import_review(self.store,other['task_id'],task['file'])

    def test_no_admission_without_real_decisions(self):
        with self.assertRaisesRegex(ContractError,'NO_ADMITTED_RECORDS'):
            admit_dataset(self.store,self.dataset,{'dataset_id':self.dataset,'reviewer_id':'A','record_decisions':[]})

    def test_missing_labels_stay_null_and_inventory_kept(self):
        evidence={'dataset_id':self.dataset,'reviewer_id':'A','record_decisions':[
            {'record_id':'demo-record-0','status':'accepted','reason':'test'}]}
        admission=admit_dataset(self.store,self.dataset,evidence)
        labels=freeze_labels(self.store,admission,self.rubric,[])
        result=compute_metrics(self.store,labels,read_json(PROJECT/'configs/metrics.v1.json'))
        rows=self.store.get(result)['payload']['rows']
        self.assertEqual(len(rows),7)
        self.assertTrue(all(r['aiv'] is None for r in rows))
        self.assertEqual(sum(r['candidate_turns'] for r in rows),2)

    def test_bad_model_evidence_and_ids_rejected(self):
        task=export_request(self.store,self.dataset,self.rubric,{'name':'test','revision':'1','parameters':{}},PROJECT/'prompts/认知预标注_v1.md')
        request=read_json(task['file'])
        self.assertTrue(all(set(r)=={'turn_id','question','prior_context'} for r in request['rows']))
        rows=[{'turn_id':r['turn_id'],'label':1,'evidence':'invented','reason':'test','outsourcing':'no',
               'insufficient_evidence':False,'self_reported_confidence':.5} for r in request['rows']]
        response=Path(self.temp.name)/'response.json'
        write_json(response,{'task_id':task['task_id'],'predictions':rows,'usage':None})
        with self.assertRaisesRegex(ContractError,'EVIDENCE_NOT_IN_QUESTION'): import_predictions(self.store,task['task_id'],response)
        rows[0]['turn_id']='unknown'
        write_json(response,{'task_id':task['task_id'],'predictions':rows,'usage':None})
        with self.assertRaisesRegex(ContractError,'ID_SET_MISMATCH'): import_predictions(self.store,task['task_id'],response)

    def test_request_key_changes_with_model_config(self):
        base={'name':'test','revision':'1','parameters':{}}
        one=export_request(self.store,self.dataset,self.rubric,base,PROJECT/'prompts/认知预标注_v1.md')
        two=export_request(self.store,self.dataset,self.rubric,dict(base,revision='2'),PROJECT/'prompts/认知预标注_v1.md')
        self.assertNotEqual(one['task_id'],two['task_id'])

    def test_draft_development_request_contains_versioned_rubric(self):
        draft=register_rubric(self.store,PROJECT/'docs/标注手册_v1.md')
        task=export_request(self.store,self.dataset,draft,{'name':'test','revision':'1','parameters':{}},
                            PROJECT/'prompts/认知预标注_v1.md','development')
        request=read_json(task['file'])
        self.assertEqual(request['rubric_text'],self.store.get(draft)['payload']['text'])

    def test_invalid_model_limits_rejected(self):
        for parameters in [{'top_p':1.1},{'max_output_tokens':0}]:
            with self.assertRaises(ContractError):
                export_request(self.store,self.dataset,self.rubric,{'name':'test','revision':'1','parameters':parameters},
                               PROJECT/'prompts/认知预标注_v1.md')


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.store=ArtifactStore(cls.temp.name)
        cls.result=run_demo(cls.temp.name)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def test_original_prediction_not_replaced_by_gold(self):
        quality=self.store.get(self.result['quality'])['payload']
        self.assertAlmostEqual(quality['unweighted']['accuracy'],11/12)
        labels=self.store.get(self.result['labels'])['payload']
        self.assertEqual(labels['rows'][0]['label'],1)
        self.assertTrue(quality['passed'])

    def test_descriptive_report_no_raw_text_identifiers_or_false_causal_claim(self):
        report=Path(self.result['report']['markdown']).read_text(encoding='utf-8')
        self.assertIn('合成数据演示',report)
        self.assertIn('未估计 AI 的因果增量效应',report)
        self.assertNotIn('SYNTHETIC-0',report)
        self.assertNotIn('请比较两种方法',report)
        self.store.verify_tree(self.result['report']['artifact_id'])

    def test_model_quality_attestation_blocks_adoption(self):
        result=self.result
        policy={'min_n':2,'min_coverage':1,'min_linear_kappa':.6,'audit_unseen':False,'attested_by':'SYNTHETIC'}
        quality=evaluate_quality(self.store,result['predictions'],result['gold'],policy)
        with self.assertRaisesRegex(ContractError,'MODEL_QUALITY_BLOCKED'):
            freeze_labels(self.store,result['admission'],result['rubric'],[],result['predictions'],quality)

    def test_bootstrap_and_sensitivity_present_without_ranking(self):
        observed=self.store.get(self.result['observed'])['payload']['rows'][0]
        self.assertEqual(observed['components'],6)
        self.assertEqual(observed['sampling_ci_95'],[1,1])
        self.assertEqual(len(self.store.get(self.result['sensitivity'])['payload']['rows']),8)
        self.assertTrue(all(r['rank'] is None for r in self.store.get(self.result['metrics'])['payload']['rows']))

    def test_unconfirmed_quality_reports_metrics_but_blocks_adoption(self):
        policy={'min_n':None,'min_coverage':None,'min_linear_kappa':None,
                'audit_unseen':True,'attested_by':'SYNTHETIC'}
        for key in (None, 'min_n', 'min_coverage', 'min_linear_kappa'):
            partial = dict(policy) if key is None else dict(min_n=2, min_coverage=1, min_linear_kappa=.6,
                                                           audit_unseen=True, attested_by='SYNTHETIC')
            if key: partial[key] = None
            quality=evaluate_quality(self.store,self.result['predictions'],self.result['gold'],partial)
            value=self.store.get(quality)['payload']
            self.assertEqual(value['paired_n'],12)
            self.assertIsNotNone(value['design_weighted']['linear_kappa'])
            self.assertIn('quality_criteria_not_confirmed',value['blocked_reasons'])
            self.assertFalse(value['passed'])
            with self.assertRaisesRegex(ContractError,'MODEL_QUALITY_BLOCKED'):
                freeze_labels(self.store,self.result['admission'],self.result['rubric'],[],self.result['predictions'],quality)

    def test_explicit_quality_policy_has_no_hardcoded_real_data_floor(self):
        # Only fabricate the non-synthetic policy route; no actual student data is read.
        original=self.store.get(self.result['predictions'])['payload']
        data=self.store.get(original['dataset'])['payload']
        dataset=self.store.put('dataset',dict(data,synthetic=False))
        pred=self.store.put('predictions',dict(original,dataset=dataset),[dataset])
        gold=self.store.put('gold',dict(self.store.get(self.result['gold'])['payload'],dataset=dataset),[dataset])
        policy=dict(min_n=2,min_coverage=.8,min_linear_kappa=.5,audit_unseen=True,attested_by='SYNTHETIC TEST')
        quality=evaluate_quality(self.store,pred,gold,policy)
        self.assertTrue(self.store.get(quality)['payload']['passed'])
        policy['min_n']=100
        rejected=self.store.get(evaluate_quality(self.store,pred,gold,policy))['payload']
        self.assertIn('insufficient_pairs',rejected['blocked_reasons'])

    def test_legacy_sensitivity_separates_terms_including_empty_scores(self):
        original=self.store.get(self.result['metrics'])['payload']
        base=dict(next(r for r in original['rows'] if r['aiv'] is not None))
        rows=[dict(base,term='fall',hot=0,ctq=0,dhi=0,aiv=0),
              dict(base,term='spring',hot=1,ctq=1,dhi=1,aiv=100),
              dict(base,term='empty',hot=None,ctq=None,dhi=None,aiv=None)]
        metric=self.store.put('metrics',dict(original,rows=rows,population_counts={'fall':1,'spring':1,'empty':1}),
                              [self.result['labels']],original['spec'])
        result=self.store.get(sensitivity(self.store,metric))['payload']['rows']
        primary={r['term']:r for r in result if r['scenario']=='primary'}
        self.assertEqual(set(primary),{'fall','spring','empty'})
        self.assertEqual(primary['fall']['mean_aiv'],0)
        self.assertEqual(primary['spring']['mean_aiv'],100)
        self.assertEqual(primary['empty']['students'],0)
        self.assertIsNone(primary['empty']['mean_aiv'])


class FormulaAndCliTests(unittest.TestCase):
    def test_weighted_confusion_and_degenerate_kappa(self):
        score=agreement([(1,1,3),(2,1,1)])
        self.assertEqual(score['accuracy'],.75)
        self.assertEqual(score['confusion'][0][0],3)
        self.assertIsNone(agreement([(1,1,1)])['kappa'])
        self.assertIsNone(agreement([])['accuracy'])

    def test_causal_command_blocked_with_receipt(self):
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()) as output:
            code=main(['--root',root,'analysis-causal'])
            value=json.loads(output.getvalue())
            self.assertEqual(code,2)
            self.assertEqual(value['status'],'blocked')
            self.assertIn('CAUSAL_NOT_IDENTIFIED',value['error'])
            self.assertEqual(len(list((Path(root)/'jobs').glob('*.json'))),1)

    def test_duplicate_json_key_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            file=Path(root)/'bad.json'
            file.write_text('{"x":1,"x":2}',encoding='utf-8')
            with self.assertRaisesRegex(ContractError,'DUPLICATE_JSON_KEY'): read_json(file)

    def test_na_prediction_still_requires_string_evidence(self):
        with self.assertRaisesRegex(ContractError,'INVALID_EVIDENCE_TYPE'): label_check(None,123,'q',True,'no evidence')


if __name__=='__main__': unittest.main()
