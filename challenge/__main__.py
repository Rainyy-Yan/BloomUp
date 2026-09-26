"""Versioned offline workflow. Existing challenge.pipeline stays compatible."""

import argparse
from datetime import datetime, timezone
import json
import sys
import uuid

from .contracts import ContractError, read_json, require
from .storage import ArtifactStore, implementation_hash
from .pipeline import write_json
from .ingest import register_run, admit_dataset
from .annotation.reviews import register_rubric, create_task, import_review, adjudicate
from .annotation.predictions import export_request, import_predictions
from .analysis import freeze_labels, compute_metrics, sensitivity
from .evaluation import evaluate_quality, observed_analysis
from .reporting import build_report
from .uncertainty import label_sensitivity


def parser():
    p=argparse.ArgumentParser(description='赛题一离线分析工作台；不调用模型，不生成正式成绩。')
    p.add_argument('--root',default='local_state',help='状态目录，含内部数据，不得直接发布')
    subs=p.add_subparsers(dest='command',required=True)
    definitions={
        'dataset-import':['run'], 'dataset-admit':['dataset','evidence'], 'parse-template':['dataset'],
        'rubric-register':['file'], 'review-export':['dataset','rubric','pool','reviewer'],
        'review-import':['task','file'], 'adjudicate':['review_a','review_b','resolutions','actor'],
        'prediction-export':['dataset','rubric','model','prompt'], 'prediction-import':['task','file'],
        'quality-evaluate':['predictions','gold','policy'], 'labels-freeze':['admission','rubric'],
        'metrics-compute':['labels','spec'], 'analysis-observed':['metrics'], 'analysis-sensitivity':['metrics'],
        'analysis-label-sensitivity':['metrics'],
        'analysis-causal':[], 'causal-panel-import':['file'], 'causal-audit':['panel','protocol'],
        'causal-report':['analysis'], 'causal-demo':[], 'causal-validate':[], 'causal-run':['file','protocol'],
        'report-build':['metrics'], 'verify':['artifact'], 'status':[], 'demo':[]}
    for name,keys in definitions.items():
        sub=subs.add_parser(name)
        for key in keys: sub.add_argument('--'+key.replace('_','-'),required=True)
        if name=='rubric-register': sub.add_argument('--frozen-by')
        if name=='demo': sub.add_argument('--formula-version',choices=['legacy-v1','aiv-v2'],default='aiv-v2')
        if name=='prediction-export': sub.add_argument('--pool',choices=['development','audit','risk','all'],default='development')
        if name=='labels-freeze':
            sub.add_argument('--gold',action='append',default=[])
            sub.add_argument('--predictions')
            sub.add_argument('--quality')
        if name=='analysis-causal': sub.add_argument('--audit')
        if name=='causal-validate': sub.add_argument('--simulations',type=int,default=100)
        if name in ['analysis-observed','analysis-label-sensitivity','analysis-causal','causal-validate','causal-run']:
            sub.add_argument('--seed',type=int,default=20260925)
            sub.add_argument('--replicates',type=int,default=200 if name=='causal-validate' else 1000)
        if name=='analysis-label-sensitivity': sub.add_argument('--error-masses',nargs='+',type=float,default=[0,.1,.2,.3])
        if name=='report-build':
            sub.add_argument('--observed')
            sub.add_argument('--sensitivity')
            sub.add_argument('--label-sensitivity')
    return p


def dispatch(store,a):
    command=a.command
    if command=='dataset-import': return register_run(store,a.run)
    if command=='dataset-admit': return admit_dataset(store,a.dataset,read_json(a.evidence))
    if command=='parse-template':
        data=store.get(a.dataset,'dataset')['payload']
        path=store.root/'workspaces'/uuid.uuid4().hex/'parse_evidence.json'
        path.parent.mkdir(parents=True)
        write_json(path,{'dataset_id':a.dataset,'reviewer_id':'','scope_status':'provisional',
                         'scope_evidence':'','accept_remaining_format_ok':False,'policy_reason':'',
                         'record_decisions':[{'record_id':rid,'status':'pending','reason':''} for rid in data['required_parse_sample']]})
        return {'file':str(path),'note':'人工核对源记录后填写 accepted/rejected；未审查不得填写 accepted。'}
    if command=='rubric-register': return register_rubric(store,a.file,a.frozen_by)
    if command=='review-export': return create_task(store,a.dataset,a.rubric,a.pool,a.reviewer)
    if command=='review-import': return import_review(store,a.task,a.file)
    if command=='adjudicate': return adjudicate(store,a.review_a,a.review_b,read_json(a.resolutions),a.actor)
    if command=='prediction-export': return export_request(store,a.dataset,a.rubric,read_json(a.model),a.prompt,a.pool)
    if command=='prediction-import': return import_predictions(store,a.task,a.file)
    if command=='quality-evaluate': return evaluate_quality(store,a.predictions,a.gold,read_json(a.policy))
    if command=='labels-freeze': return freeze_labels(store,a.admission,a.rubric,a.gold,a.predictions,a.quality)
    if command=='metrics-compute': return compute_metrics(store,a.labels,read_json(a.spec))
    if command=='analysis-observed': return observed_analysis(store,a.metrics,a.seed,a.replicates)
    if command=='analysis-sensitivity': return sensitivity(store,a.metrics)
    if command=='analysis-label-sensitivity': return label_sensitivity(store,a.metrics,a.error_masses,a.seed,a.replicates)
    if command in ('causal-panel-import','causal-audit','analysis-causal','causal-report'):
        from .causal import import_panel, audit_panel, estimate_effect, build_causal_report
        if command=='causal-panel-import': return import_panel(store,read_json(a.file))
        if command=='causal-audit': return audit_panel(store,a.panel,read_json(a.protocol))
        if command=='causal-report': return build_causal_report(store,a.analysis)
        require(a.audit is not None,'CAUSAL_NOT_IDENTIFIED','a frozen causal audit is required')
        return estimate_effect(store,a.audit,a.seed,a.replicates)
    if command=='causal-run':
        from .causal_workflow import run_causal_workflow
        return run_causal_workflow(store,read_json(a.file),read_json(a.protocol),a.seed,a.replicates)
    if command=='causal-demo':
        from .causal_demo import run_causal_demo
        return run_causal_demo(store)
    if command=='causal-validate':
        from .causal_demo import validate_causal_simulation
        return validate_causal_simulation(store,a.simulations,a.replicates,a.seed)
    if command=='report-build': return build_report(store,a.metrics,a.observed,a.sensitivity,a.label_sensitivity)
    if command=='verify':
        store.verify_tree(a.artifact)
        return {'artifact':a.artifact,'verified':True}
    if command=='status':
        counts={}
        for path in sorted((store.root/'artifacts').glob('*/*.json')):
            value=store.get(path.stem)
            kind=value['artifact_type']
            counts[kind]=counts.get(kind,0)+1
        return {'artifact_counts':counts,'mode':'offline_only','formal_scoring_enabled':False}
    if command=='demo':
        from .demo import run_demo
        return run_demo(store.root/'synthetic_demo'/uuid.uuid4().hex, a.formula_version)
    raise ContractError('UNKNOWN_COMMAND')


def main(argv=None):
    args=parser().parse_args(argv)
    store=ArtifactStore(args.root)
    job_id=uuid.uuid4().hex
    jobs=store.root/'jobs'
    jobs.mkdir(exist_ok=True)
    receipt={'job_id':job_id,'command':args.command,'started_at':datetime.now(timezone.utc).isoformat(),
             'code_hash':implementation_hash(),'status':'running'}
    path=jobs/f'{job_id}.json'
    write_json(path,receipt)
    code=0
    try:
        result=dispatch(store,args)
        receipt.update(status='completed',result=result)
        if args.command=='causal-run' and result['analysis_status']=='blocked':
            receipt.update(status='blocked',error='CAUSAL_NOT_IDENTIFIED: see audit and report')
            code=2
    except ContractError as exc:
        receipt.update(status='blocked',error=str(exc))
        code=2
    except (OSError,ValueError,TypeError,KeyError,AttributeError,IndexError) as exc:
        # Avoid logging private payloads or raw exception arguments.
        receipt.update(status='failed',error=type(exc).__name__+': invalid input or local IO; inspect input schema and paths')
        code=1
    receipt['finished_at']=datetime.now(timezone.utc).isoformat()
    write_json(path,receipt)
    print(json.dumps(receipt,ensure_ascii=False,allow_nan=False))
    return code


if __name__=='__main__': sys.exit(main())
