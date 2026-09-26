"""Versioned, opt-in inference with persistent budget and conservative recovery.

The journal is local, not a provider quota. Unknown delivery is never replayed.
Success enters the existing prediction importer, never the human gold pipeline.
"""

from contextlib import contextmanager
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import uuid

from .annotation.predictions import import_predictions, validate_prediction
from .contracts import ContractError, canonical, file_hash, fingerprint, read_json, require
from .providers import MINIMAX_ENDPOINT, check_credentials, decode_prediction, responses_payload, send


CONFIG_FIELDS = {'provider','endpoint','api_key_env','budget_id','budget_cny','prior_spend_cny',
                 'input_cny_per_million','output_cny_per_million','price_basis',
                 'max_requests','max_attempts','timeout_seconds'}


def money(value):
    require(type(value) in (str, int, float), 'INVALID_MONEY')
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ContractError('INVALID_MONEY') from exc
    require(amount.is_finite() and amount >= 0, 'INVALID_MONEY')
    return amount


def validate_config(config):
    require(isinstance(config, dict) and set(config) == CONFIG_FIELDS, 'INVALID_INFERENCE_CONFIG')
    config = dict(config)
    require(config['provider'] in ('synthetic','minimax'), 'PROVIDER_NOT_ALLOWED')
    require(config['endpoint'] == (MINIMAX_ENDPOINT if config['provider'] == 'minimax' else None), 'PROVIDER_NOT_ALLOWED')
    for key in ('api_key_env','budget_id'):
        require(isinstance(config[key], str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,79}', config[key]), 'INVALID_CONFIG_NAME')
    require(isinstance(config['price_basis'], str) and config['price_basis'].strip(), 'PRICE_BASIS_REQUIRED')
    for key in ('budget_cny','prior_spend_cny','input_cny_per_million','output_cny_per_million'):
        config[key] = str(money(config[key]))
    require(money(config['budget_cny']) > 0, 'BUDGET_REQUIRED')
    require(money(config['prior_spend_cny']) <= money(config['budget_cny']), 'PRIOR_SPEND_EXCEEDS_BUDGET')
    if config['provider'] == 'minimax':
        require(all(money(config[k]) > 0 for k in ('input_cny_per_million','output_cny_per_million')), 'PRICE_REQUIRED')
    for key, upper in [('max_requests', 10000), ('max_attempts', 3), ('timeout_seconds', 60)]:
        require(type(config[key]) is int and 1 <= config[key] <= upper, 'INVALID_INFERENCE_LIMIT')
    return config


def charge(config, input_tokens, output_tokens):
    return (Decimal(input_tokens)*money(config['input_cny_per_million']) +
            Decimal(output_tokens)*money(config['output_cny_per_million']))/1_000_000


def create_plan(store, task_id, config):
    """Freeze a complete exported task. Planning never reads a key or sends data."""
    config = validate_config(config)
    store.verify_tree(task_id)
    task = store.get(task_id, 'prediction_task')['payload']
    dataset = store.get(task['dataset'], 'dataset')['payload']
    if config['provider'] == 'synthetic':
        require(dataset.get('synthetic') is True, 'SYNTHETIC_PROVIDER_REQUIRES_SYNTHETIC_DATA')
    require(0 < len(task['rows']) <= config['max_requests'], 'REQUEST_LIMIT_EXCEEDED')
    rubric = store.get(task['rubric'], 'rubric')['payload']
    require(task['pool'] == 'development' or rubric['status'] == 'frozen', 'RUBRIC_NOT_FROZEN')
    rows = []
    for i, row in enumerate(task['rows'], 1):
        body = responses_payload(task, rubric['text'], row, f'row-{i:06}')
        limit = body['max_output_tokens']
        require(type(limit) is int and 1 <= limit <= 8192, 'INVALID_TOKEN_LIMIT')
        size = len(canonical(body))
        require(size <= 65536, 'REQUEST_TOO_LARGE')
        # Engineering reserve only: not a tokenizer guarantee or provider billing cap.
        bound = size + 4096
        rows.append(dict(turn_id=row['turn_id'], alias=f'row-{i:06}', body=body,
                         request_hash=fingerprint([config['endpoint'], body]), input_reserve_tokens=bound,
                         reserve_cny=str(charge(config, bound, limit))))
    require(len({r['turn_id'] for r in rows}) == len(rows), 'DUPLICATE_REQUEST_ID')
    return store.put('inference_plan', dict(task=task_id, config=config, rows=rows,
                                           synthetic=dataset.get('synthetic') is True,
                                           scope='candidate_predictions_only', model=task['model'],
                                           price_status='configured_estimate_not_invoice'), [task_id], config)


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent/f'.{uuid.uuid4().hex}.tmp'
    try:
        with temp.open('xb') as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists(): temp.unlink()


def save_ledger(path, ledger):
    ledger['checksum'] = fingerprint({k:v for k,v in ledger.items() if k != 'checksum'})
    save(path, ledger)


@contextmanager
def runner_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    lock = root/'.runner.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ContractError('INFERENCE_BUSY: inspect lock owner before recovery') from exc
    try:
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd)
        lock.unlink()


def _usage(raw, row):
    usage = raw.get('usage')
    require(isinstance(usage, dict), 'USAGE_UNVERIFIED')
    clean = {}
    for key, bound in [('input_tokens', row['input_reserve_tokens']),
                       ('output_tokens', row['body']['max_output_tokens'])]:
        value = usage.get(key)
        require(type(value) is int and 0 <= value <= bound, 'USAGE_UNVERIFIED')
        clean[key] = value
    return clean


def _prediction(raw, request, task_row):
    require(raw.get('status') in (None, 'completed'), 'INCOMPLETE_RESPONSE')
    parts = [c['text'] for item in raw.get('output', []) if item.get('type') == 'message'
             for c in item.get('content', []) if c.get('type') == 'output_text']
    require(len(parts) == 1, 'INVALID_RESPONSE_TEXT')
    result = decode_prediction(parts[0])
    require(result.get('turn_id') == request['alias'], 'PREDICTION_ID_MISMATCH')
    result['turn_id'] = request['turn_id']
    validate_prediction(result, task_row)
    return result


def _spend(ledger, budget_id):
    total = money(ledger['budgets'][budget_id]['prior_spend_cny'])
    for run in ledger['runs'].values():
        if run['budget_id'] == budget_id:
            total += sum((money(a['charge_cny']) for a in run['attempts']), Decimal(0))
    return total


def _verify_cache(store, ledger):
    """Check every persisted response before trusting cached usage or success."""
    for run in ledger['runs'].values():
        for attempt in run['attempts']:
            if 'response_file' in attempt:
                path = store.root/'inference'/attempt['response_file']
                require(path.resolve().is_relative_to(store.root/'inference'), 'CACHE_PATH_ESCAPE')
                require(path.is_file() and file_hash(path) == attempt['response_hash'], 'CACHE_HASH_MISMATCH')


def snapshot_plan(store, plan_id):
    """Read validated cached rows without retrying or resolving unknown charges."""
    store.verify_tree(plan_id)
    plan = store.get(plan_id, 'inference_plan')['payload']
    task = store.get(plan['task'], 'prediction_task')['payload']
    index = {row['turn_id']:row for row in task['rows']}
    root = store.root/'inference'
    with runner_lock(root):
        ledger = read_json(root/'ledger.json')
        require(ledger.get('checksum') == fingerprint({k:v for k,v in ledger.items() if k != 'checksum'}),
                'LEDGER_CHECKSUM_MISMATCH')
        require(ledger.get('version') == 1, 'LEDGER_VERSION_MISMATCH')
        require(plan_id in ledger['runs'], 'PLAN_NOT_EXECUTED')
        _verify_cache(store, ledger)
        attempts = ledger['runs'][plan_id]['attempts']
        rows, unavailable, statuses = [], [], []
        for request in plan['rows']:
            selected = [a for a in attempts if a['request_hash'] == request['request_hash']]
            success = next((a for a in selected if a['status'] == 'validated'), None)
            if success:
                rows.append(_prediction(read_json(root/success['response_file']),request,index[request['turn_id']]))
                statuses.append('validated')
            else:
                status = selected[-1]['status'] if selected else 'not_attempted'
                require(status in ('validation_failed','submitted_unknown','usage_unverified','not_attempted'),
                        'INVALID_ATTEMPT_STATUS')
                statuses.append(status)
                unavailable.append(dict(turn_id=request['turn_id'],status=status))
        return store.put('prediction_snapshot', dict(
            task=plan['task'], dataset=task['dataset'], rubric=task['rubric'], plan=plan_id,
            rows=sorted(rows,key=lambda row:row['turn_id']), unavailable=unavailable,
            status_counts=dict(Counter(statuses)), attempts=attempts,
            estimated_spend_cny=str(_spend(ledger,plan['config']['budget_id'])),
            billing_status='estimate_and_unresolved_reserves_not_invoice', network_calls=0,
            confidence_status='self_reported_uncalibrated',
            completion='partial' if unavailable else 'complete_snapshot'), [plan_id])


def run_plan(store, plan_id, execute=False, allow_network=False, retry_invalid=False, continue_on_invalid=False):
    store.verify_tree(plan_id)
    plan = store.get(plan_id, 'inference_plan')['payload']
    config = validate_config(plan['config'])
    if not execute:
        return dict(status='planned', plan=plan_id, requests=len(plan['rows']), provider=config['provider'],
                    budget_cny=config['budget_cny'], network_called=False,
                    reserve_for_one_attempt_each_cny=str(sum((money(r['reserve_cny']) for r in plan['rows']), Decimal(0))))
    if config['provider'] == 'minimax':
        require(allow_network, 'NETWORK_NOT_AUTHORIZED')
        check_credentials(config)
    task = store.get(plan['task'], 'prediction_task')['payload']
    index = {r['turn_id']:r for r in task['rows']}
    root = store.root/'inference'
    with runner_lock(root):
        ledger_path = root/'ledger.json'
        ledger = read_json(ledger_path) if ledger_path.exists() else dict(version=1, budgets={}, runs={})
        if ledger_path.exists():
            require(ledger.get('checksum') == fingerprint({k:v for k,v in ledger.items() if k != 'checksum'}),
                    'LEDGER_CHECKSUM_MISMATCH')
        require(ledger.get('version') == 1, 'LEDGER_VERSION_MISMATCH')
        budget_id = config['budget_id']
        definition = {k:config[k] for k in ('budget_cny','prior_spend_cny')}
        if budget_id in ledger['budgets']:
            require(ledger['budgets'][budget_id] == definition, 'BUDGET_DEFINITION_CHANGED')
        else:
            ledger['budgets'][budget_id] = definition
        run = ledger['runs'].setdefault(plan_id, dict(budget_id=budget_id, attempts=[]))
        _verify_cache(store, ledger)

        def finish(status, **extra):
            save_ledger(ledger_path, ledger)
            result = dict(plan=plan_id, status=status, attempts=len(run['attempts']),
                          successes=sum(a['status'] == 'validated' for a in run['attempts']),
                          estimated_spend_cny=str(_spend(ledger, budget_id)),
                          billing_status='estimate_and_unresolved_reserves_not_invoice',
                          synthetic=plan['synthetic'], **extra)
            artifact = store.put('inference_run', dict(result, attempts=run['attempts']), [plan_id])
            return dict(result, run_artifact=artifact)

        if any(a['status'] in ('submitted_unknown','usage_unverified')
               for r in ledger['runs'].values() if r['budget_id'] == budget_id for a in r['attempts']):
            return finish('budget_uncertain', error_code='RECONCILE_WITH_PROVIDER_BEFORE_NEW_CALLS')

        predictions, failed_turn_ids = [], []
        for row in plan['rows']:
            attempts = [a for a in run['attempts'] if a['request_hash'] == row['request_hash']]
            success = next((a for a in attempts if a['status'] == 'validated'), None)
            if success:
                raw = read_json(root/success['response_file'])
                predictions.append(_prediction(raw, row, index[row['turn_id']]))
                continue
            while True:
                if attempts and (not retry_invalid or len(attempts) >= config['max_attempts']):
                    if continue_on_invalid:
                        failed_turn_ids.append(row['turn_id'])
                        break
                    return finish('validation_failed', error_code='INVALID_PREDICTION')
                if _spend(ledger, budget_id) + money(row['reserve_cny']) > money(config['budget_cny']):
                    return finish('budget_exhausted', error_code='RESERVATION_EXCEEDS_BUDGET')
                attempt = dict(request_hash=row['request_hash'], alias=row['alias'],
                               status='submitted_unknown', started_at=datetime.now(timezone.utc).isoformat(),
                               charge_cny=row['reserve_cny'], reserved_cny=row['reserve_cny'],
                               usage=None, error_code=None)
                run['attempts'].append(attempt)
                attempts.append(attempt)
                # Persist the reservation before calling the provider. A crash is ambiguous, not free.
                save_ledger(ledger_path, ledger)
                try:
                    raw = send(row['body'], config)
                    require(isinstance(raw, dict), 'INVALID_PROVIDER_RESPONSE')
                    response_path = root/plan_id[-24:]/f'response-{len(run["attempts"]):06}.json'
                    save(response_path, raw)
                    attempt.update(response_file=response_path.relative_to(root).as_posix(),
                                   response_hash=file_hash(response_path),
                                   finished_at=datetime.now(timezone.utc).isoformat())
                except (OSError, ValueError, TypeError, KeyError, AttributeError):
                    attempt['error_code'] = 'TRANSPORT_OR_RESPONSE_UNKNOWN'
                    return finish('submitted_unknown', error_code=attempt['error_code'])
                try:
                    # A different model invalidates both provenance and configured prices.
                    require(raw.get('model') == plan['model']['name'], 'RESPONSE_MODEL_MISMATCH')
                    usage = _usage(raw, row)
                except (ValueError, TypeError, KeyError, AttributeError):
                    attempt.update(status='usage_unverified', error_code='USAGE_UNVERIFIED')
                    return finish('usage_unverified', error_code='USAGE_UNVERIFIED')
                attempt.update(usage=usage, charge_cny=str(charge(config, usage['input_tokens'], usage['output_tokens'])))
                try:
                    prediction = _prediction(raw, row, index[row['turn_id']])
                except (ValueError, TypeError, KeyError, AttributeError):
                    attempt.update(status='validation_failed', error_code='INVALID_PREDICTION')
                    save_ledger(ledger_path, ledger)
                    if retry_invalid and len(attempts) < config['max_attempts']:
                        continue
                    if continue_on_invalid:
                        failed_turn_ids.append(row['turn_id'])
                        break
                    return finish('validation_failed', error_code='INVALID_PREDICTION')
                attempt.update(status='validated', error_code=None)
                save_ledger(ledger_path, ledger)
                predictions.append(prediction)
                break
        usage = {key:sum(a['usage'][key] for a in run['attempts']) for key in ('input_tokens','output_tokens')}
        if failed_turn_ids:
            partial = store.put('partial_predictions', dict(
                task=plan['task'], dataset=task['dataset'], rubric=task['rubric'], plan=plan_id,
                rows=sorted(predictions, key=lambda item:item['turn_id']),
                failed_turn_ids=sorted(failed_turn_ids), usage=usage,
                attempts=run['attempts'], completion='partial',
                confidence_status='self_reported_uncalibrated'), [plan_id])
            return finish('completed_with_failures', partial_predictions=partial,
                          failed_predictions=len(failed_turn_ids), usage=usage,
                          independent_quality_status='not_evaluated')
        batch_path = root/plan_id[-24:]/'predictions.json'
        save(batch_path, dict(task_id=plan['task'], predictions=predictions, usage=usage))
        artifact = import_predictions(store, plan['task'], batch_path)
        return finish('completed', predictions=artifact, usage=usage, independent_quality_status='not_evaluated')
