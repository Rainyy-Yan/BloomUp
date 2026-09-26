"""Explicit synthetic fixture, isolated from all real prepared runs."""

import csv
from pathlib import Path

from .storage import ArtifactStore
from .pipeline import write_json, write_csv
from .ingest import admit_dataset
from .annotation.reviews import register_rubric, create_task, import_review, adjudicate, IMMUTABLE, EDITABLE
from .annotation.predictions import export_request, import_predictions
from .evaluation import evaluate_quality, observed_analysis
from .analysis import freeze_labels, compute_metrics, sensitivity
from .reporting import build_report
from .uncertainty import label_sensitivity
from .contracts import read_json, require


def synthetic_dataset(store):
    turns=[]
    for i in range(6):
        for j in range(1,3):
            turns.append({'turn_id':f'demo-{i}-{j}','student_key':f'SYNTHETIC-{i}','record_id':f'demo-record-{i}',
                          'term':'synthetic_term','turn_index':j,'question':f'合成问题 {i}-{j}：请比较两种方法。',
                          'prior_context':'','agent_id':'synthetic_agent','label':None})
    ids=[x['turn_id'] for x in turns]
    return store.put('dataset',{'turns':turns,'records':[{'record_id':f'demo-record-{i}','parse_status':'format_ok_pending_review'} for i in range(6)],
                                'sampling':{'selected':{'development':ids,'audit':ids,'risk':[]},
                                            'selection_info':{x['turn_id']:{'design_weight':1,'component_id':x['student_key']} for x in turns},
                                            'component_by_student':{x['student_key']:x['student_key'] for x in turns}},
                                'inventory':[{'term':'synthetic_term','student_key':f'SYNTHETIC-{i}'} for i in range(7)],
                                'required_parse_sample':[],'scope_status':'provisional','synthetic':True,
                                'note':'SYNTHETIC ONLY. Sample pools intentionally overlap for test coverage; not an independent evaluation.'})


def fill_synthetic_review(path, levels):
    path=Path(path)
    with Path(path).open(encoding='utf-8-sig',newline='') as stream: rows=list(csv.DictReader(stream))
    for row,level in zip(rows,levels):
        row.update(bloom_level=str(level),evidence_quote='请比较两种方法',outsourcing='no',insufficient_evidence='no',
                   confidence_1_to_5='4',reviewed_at='2026-09-25T12:00:00+08:00',notes='合成测试标记，非人工评审结果')
    write_csv(path,rows,IMMUTABLE+EDITABLE)


def run_demo(root, formula_version='legacy-v1'):
    require(formula_version in ('legacy-v1', 'aiv-v2'), 'UNKNOWN_FORMULA_VERSION')
    store=ArtifactStore(root)
    dataset=synthetic_dataset(store)
    project=Path(__file__).resolve().parents[1]
    rubric=register_rubric(store,project/'docs/标注手册_v1.md','SYNTHETIC-FREEZE-ACTOR')
    tasks=[create_task(store,dataset,rubric,'audit',f'SYNTHETIC-RATER-{x}') for x in ['A','B']]
    levels=[1,4,2,5,3,6,1,4,2,5,3,6]
    reviews=[]
    for task in tasks:
        fill_synthetic_review(task['file'],levels)
        reviews.append(import_review(store,task['task_id'],task['file']))
    gold=adjudicate(store,*reviews,[],'SYNTHETIC-ADJUDICATOR')
    evidence={'dataset_id':dataset,'reviewer_id':'SYNTHETIC-PARSE-ACTOR','scope_status':'provisional',
              'record_decisions':[{'record_id':f'demo-record-{i}','status':'accepted','reason':'Synthetic fixture'} for i in range(6)]}
    admission=admit_dataset(store,dataset,evidence)
    task=export_request(store,dataset,rubric,{'name':'synthetic-fixture','revision':'v1','parameters':{'temperature':0}},project/'prompts/认知预标注_v1.md')
    turns=store.get(dataset)['payload']['turns']
    predictions=[{'turn_id':t['turn_id'],'label':level,'evidence':'请比较两种方法','reason':'Synthetic fixture',
                  'outsourcing':'no','insufficient_evidence':False,'self_reported_confidence':.8} for t,level in zip(turns,levels)]
    predictions[0]['label']=2  # Deliberate error; gold must not overwrite model evaluation.
    response=Path(task['file']).with_name('synthetic_response.json')
    write_json(response,{'task_id':task['task_id'],'predictions':predictions,'usage':None})
    prediction=import_predictions(store,task['task_id'],response)
    quality=evaluate_quality(store,prediction,gold,{'min_n':2,'min_coverage':1,'min_linear_kappa':.6,
                                                  'audit_unseen':True,'attested_by':'SYNTHETIC-TEST-NOT-REAL-ATTESTATION'})
    labels=freeze_labels(store,admission,rubric,[gold],prediction,quality)
    spec = read_json(project / 'configs' / ('metrics.v2.json' if formula_version == 'aiv-v2' else 'metrics.v1.json'))
    if formula_version == 'aiv-v2':
        spec.update(agent_catalog=['synthetic_agent'], agent_catalog_status='provisional',
                    reference_rationale='Synthetic demonstration target only; not a validated course distribution.')
    metrics=compute_metrics(store,labels,spec)
    observed=observed_analysis(store,metrics,replicates=200)
    weights=sensitivity(store,metrics)
    noise=label_sensitivity(store,metrics,replicates=200)
    report=build_report(store,metrics,observed,weights,noise)
    result=dict(synthetic=True,dataset=dataset,rubric=rubric,admission=admission,gold=gold,predictions=prediction,
                quality=quality,labels=labels,metrics=metrics,observed=observed,sensitivity=weights,label_sensitivity=noise,report=report)
    write_json(store.root/'demo_result.json',result)
    return result
