"""Offline model request export and validated response import; no network calls."""

from pathlib import Path
import uuid

from ..contracts import require, read_json, fingerprint, label_check, finite_number
from ..pipeline import write_json


def validate_prediction(row, source):
    """Shared per-row contract for offline imports and controlled inference."""
    fields = {'turn_id','label','evidence','reason','outsourcing','insufficient_evidence','self_reported_confidence'}
    require(isinstance(row, dict) and set(row) == fields, 'INVALID_PREDICTION_FIELDS')
    require(row['turn_id'] == source['turn_id'], 'PREDICTION_ID_MISMATCH')
    label_check(row['label'], row['evidence'], source['question'], row['insufficient_evidence'], row['reason'])
    require(row['outsourcing'] in ['yes','no','uncertain'], 'INVALID_OUTSOURCING')
    finite_number(row['self_reported_confidence'], 0, 1)


def export_request(store, dataset_id, rubric_id, model_spec, prompt_path, pool='all'):
    for aid in [dataset_id,rubric_id]: store.verify_tree(aid)
    dataset = store.get(dataset_id, 'dataset')['payload']
    rubric = store.get(rubric_id, 'rubric')['payload']
    require(pool in ['all','development','audit','risk'], 'INVALID_POOL')
    require(pool=='development' or rubric['status']=='frozen', 'RUBRIC_NOT_FROZEN')
    require(set(model_spec)=={'name','revision','parameters'}, 'INVALID_MODEL_SPEC')
    require(isinstance(model_spec['name'],str) and model_spec['name'].strip(), 'MODEL_NAME_REQUIRED')
    require(isinstance(model_spec['revision'],str) and model_spec['revision'].strip(), 'MODEL_REVISION_REQUIRED')
    require(isinstance(model_spec['parameters'],dict), 'INVALID_MODEL_PARAMETERS')
    require(set(model_spec['parameters']).issubset({'temperature','top_p','max_output_tokens','seed'}), 'UNSUPPORTED_MODEL_PARAMETER')
    for key,value in model_spec['parameters'].items():
        finite_number(value, 0)
        if key in ['max_output_tokens','seed']: require(type(value) is int, 'INVALID_INTEGER_PARAMETER')
        if key=='top_p': finite_number(value,0,1)
        if key=='max_output_tokens': require(value > 0, 'INVALID_TOKEN_LIMIT')
    prompt = Path(prompt_path).read_text(encoding='utf-8-sig')
    require(prompt.strip(), 'EMPTY_PROMPT')
    schema = read_json(Path(__file__).resolve().parents[2]/'prompts/label.schema.json')
    selected = None if pool=='all' else set(dataset['sampling']['selected'][pool])
    rows = [{'turn_id':x['turn_id'],'question':x['question'],'prior_context':x['prior_context']}
            for x in dataset['turns'] if selected is None or x['turn_id'] in selected]
    require(rows, 'EMPTY_SAMPLE')
    aid = store.put('prediction_task', {'dataset': dataset_id,'rubric':rubric_id, 'model':model_spec,
                                       'prompt':prompt,'schema':schema,'rows':rows,'pool':pool,
                                       'request_key':fingerprint([rows,rubric,prompt,schema,model_spec])}, [dataset_id,rubric_id])
    workspace = store.root/'workspaces'/uuid.uuid4().hex
    workspace.mkdir(parents=True,exist_ok=False)
    # The file is internal. Explicit permission is still required before any external transmission.
    write_json(workspace/'model_request.json', {'task_id':aid,'model':model_spec,'prompt':prompt,'rubric_text':rubric['text'],
                                               'schema':schema,'rows':rows,
                                               'batch_instructions':'Apply prompt and frozen rubric per row; collect objects in predictions. Return task_id, predictions and usage. No network action is performed by this export.'})
    return {'task_id':aid,'file':str(workspace/'model_request.json')}


def import_predictions(store, task_id, path):
    store.verify_tree(task_id)
    task = store.get(task_id,'prediction_task')['payload']
    response = read_json(path)
    require(isinstance(response,dict) and set(response)=={'task_id','predictions','usage'}, 'INVALID_PREDICTION_BATCH')
    require(response['task_id']==task_id, 'PREDICTION_TASK_MISMATCH')
    usage=response['usage']
    require(usage is None or isinstance(usage,dict) and set(usage).issubset({'input_tokens','output_tokens'}), 'INVALID_USAGE')
    if usage is not None:
        for value in usage.values(): require(type(value) is int and value>=0,'INVALID_USAGE')
    index={x['turn_id']:x for x in task['rows']}
    rows=response['predictions']
    require(isinstance(rows,list) and all(isinstance(x,dict) for x in rows) and len(rows)==len(index)
            and {x.get('turn_id') for x in rows}==set(index), 'PREDICTION_ID_SET_MISMATCH')
    for row in rows:
        validate_prediction(row, index[row['turn_id']])
    return store.put('predictions', {'task':task_id,'dataset':task['dataset'],'rubric':task['rubric'],
                                     'rows':sorted(rows,key=lambda x:x['turn_id']),'usage':usage,
                                     'confidence_status':'self_reported_uncalibrated'}, [task_id])
