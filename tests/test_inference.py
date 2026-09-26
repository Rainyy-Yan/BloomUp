"""Network-free behavioral tests for the controlled prediction runner."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from challenge.__main__ import main
from challenge.annotation.predictions import export_request
from challenge.annotation.reviews import register_rubric
from challenge.contracts import ContractError, read_json
from challenge.demo import synthetic_dataset
from challenge.inference import create_plan, run_plan
from challenge.providers import decode_prediction, responses_payload, send, _NoRedirect
from challenge.storage import ArtifactStore


PROJECT = Path(__file__).resolve().parents[1]


def config(**changes):
    value = dict(provider='synthetic', endpoint=None, api_key_env='BLOOMUP_MINIMAX_API_KEY',
                 budget_id='test-budget', budget_cny='10', prior_spend_cny='0',
                 input_cny_per_million='6.3', output_cny_per_million='25.2',
                 price_basis='Synthetic test assumption, not a provider price quote',
                 max_requests=12, max_attempts=2, timeout_seconds=10)
    value.update(changes)
    return value


def response(body):
    row = json.loads(body['input'][1]['content'])
    prediction = dict(turn_id=row['turn_id'], label=4, evidence='请比较两种方法',
                      reason='Synthetic fixture', outsourcing='no', insufficient_evidence=False,
                      self_reported_confidence=.5)
    return dict(id='synthetic-response', model=body['model'], status='completed',
                output=[dict(type='message', content=[dict(type='output_text', text=json.dumps(prediction))])],
                usage=dict(input_tokens=100, output_tokens=50))


class InferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ArtifactStore(self.tmp.name)
        dataset = synthetic_dataset(self.store)
        self.rubric = register_rubric(self.store, PROJECT/'docs/标注手册_v1.md', 'SYNTHETIC')
        self.task = export_request(self.store, dataset, self.rubric,
                                   dict(name='synthetic-fixture', revision='v1', parameters={'max_output_tokens':512}),
                                   PROJECT/'prompts/认知预标注_v1.md', 'development')['task_id']

    def plan(self, **kwargs):
        return create_plan(self.store, self.task, config(**kwargs))

    def test_dry_run_never_calls_provider_or_requires_key(self):
        plan = self.plan()
        with patch('challenge.inference.send') as sender:
            result = run_plan(self.store, plan)
        sender.assert_not_called()
        self.assertEqual(result['status'], 'planned')
        self.assertEqual(result['requests'], 12)

    def test_unexpected_model_keeps_reserve_and_blocks_adoption_and_resume(self):
        plan = self.plan()
        def wrong_model(body, cfg):
            raw = response(body)
            raw['model'] = 'different-priced-model'
            return raw
        with patch('challenge.inference.send', side_effect=wrong_model) as sender:
            first = run_plan(self.store, plan, execute=True)
            second = run_plan(self.store, plan, execute=True, retry_invalid=True)
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(first['status'], 'usage_unverified')
        self.assertEqual(second['status'], 'budget_uncertain')
        ledger = read_json(self.store.root/'inference/ledger.json')
        attempt = ledger['runs'][plan]['attempts'][0]
        self.assertEqual(attempt['charge_cny'], attempt['reserved_cny'])
        self.assertFalse((self.store.root/'artifacts/predictions').exists())

    def test_success_import_and_resume_make_no_duplicate_calls(self):
        plan = self.plan()
        with patch('challenge.inference.send', side_effect=lambda body, cfg: response(body)) as sender:
            first = run_plan(self.store, plan, execute=True)
            second = run_plan(self.store, plan, execute=True)
        self.assertEqual(sender.call_count, 12)
        self.assertEqual(first['predictions'], second['predictions'])
        self.assertEqual(first['status'], 'completed')
        rows = self.store.get(first['predictions'], 'predictions')['payload']['rows']
        self.assertEqual(len(rows), 12)
        self.assertTrue(all(x['label'] == 4 for x in rows))
        self.assertFalse(list((self.store.root/'artifacts'/'gold').glob('*')))
        self.store.verify_tree(first['run_artifact'])

    def test_partial_resume_reuses_only_verified_success(self):
        plan = self.plan()
        calls = [0]
        def once(body, cfg):
            calls[0] += 1
            result = response(body)
            if calls[0] == 2:
                result['output'][0]['content'][0]['text'] = '{}'
            return result
        with patch('challenge.inference.send', side_effect=once):
            first = run_plan(self.store, plan, execute=True)
        self.assertEqual(first['status'], 'validation_failed')
        with patch('challenge.inference.send', side_effect=lambda body, cfg: response(body)) as sender:
            second = run_plan(self.store, plan, execute=True)
            self.assertEqual(second['status'], 'validation_failed')
            sender.assert_not_called()
            third = run_plan(self.store, plan, execute=True, retry_invalid=True)
        self.assertEqual(sender.call_count, 11)
        self.assertEqual(third['attempts'], 13)
        self.assertEqual(third['status'], 'completed')

    def test_bounded_invalid_retries_preserve_cost_and_stop(self):
        plan = self.plan()
        def bad(body, cfg):
            result = response(body)
            result['output'][0]['content'][0]['text'] = 'not JSON'
            return result
        with patch('challenge.inference.send', side_effect=bad) as sender:
            first = run_plan(self.store, plan, execute=True, retry_invalid=True)
            second = run_plan(self.store, plan, execute=True, retry_invalid=True)
        self.assertEqual(sender.call_count, 2)
        self.assertEqual(first['status'], 'validation_failed')
        self.assertEqual(second['status'], 'validation_failed')
        self.assertGreater(float(second['estimated_spend_cny']), 0)

    def test_transport_unknown_is_never_automatically_replayed(self):
        plan = self.plan()
        with patch('challenge.inference.send', side_effect=TimeoutError('private-key-and-text')) as sender:
            first = run_plan(self.store, plan, execute=True)
            second = run_plan(self.store, plan, execute=True, retry_invalid=True)
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(first['status'], 'submitted_unknown')
        self.assertEqual(second['status'], 'budget_uncertain')
        self.assertNotIn('private-key-and-text', (self.store.root/'inference/ledger.json').read_text())

    def test_interruption_after_dispatch_reserves_cost_and_blocks_resume(self):
        plan = self.plan()
        with patch('challenge.inference.send', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): run_plan(self.store, plan, execute=True)
        with patch('challenge.inference.send') as sender:
            result = run_plan(self.store, plan, execute=True)
        sender.assert_not_called()
        self.assertEqual(result['status'], 'budget_uncertain')

    def test_missing_usage_freezes_budget_instead_of_assuming_free(self):
        plan = self.plan()
        def missing(body, cfg):
            result = response(body)
            result.pop('usage')
            return result
        with patch('challenge.inference.send', side_effect=missing) as sender:
            result = run_plan(self.store, plan, execute=True)
            run_plan(self.store, plan, execute=True, retry_invalid=True)
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(result['status'], 'usage_unverified')

    def test_budget_refuses_call_before_dispatch(self):
        plan = self.plan(budget_cny='0.000001')
        with patch('challenge.inference.send') as sender:
            result = run_plan(self.store, plan, execute=True)
        sender.assert_not_called()
        self.assertEqual(result['status'], 'budget_exhausted')

    def test_budget_persists_across_plans_and_cannot_reset_prior_spend(self):
        plan = self.plan()
        with patch('challenge.inference.send', side_effect=lambda body, cfg: response(body)):
            first = run_plan(self.store, plan, execute=True)
        changed = self.plan(prior_spend_cny='0.01')
        with self.assertRaisesRegex(ContractError, 'BUDGET_DEFINITION_CHANGED'):
            run_plan(self.store, changed, execute=True)
        self.assertGreater(float(first['estimated_spend_cny']), 0)

    def test_changed_cache_blocks_resume(self):
        plan = self.plan()
        with patch('challenge.inference.send', side_effect=lambda body, cfg: response(body)):
            run_plan(self.store, plan, execute=True)
        raw = next((self.store.root/'inference').rglob('response-*.json'))
        raw.write_text('{}', encoding='utf-8')
        with patch('challenge.inference.send') as sender:
            with self.assertRaisesRegex(ContractError, 'CACHE_HASH_MISMATCH'):
                run_plan(self.store, plan, execute=True)
        sender.assert_not_called()

    def test_changed_budget_ledger_blocks_resume(self):
        plan = self.plan()
        with patch('challenge.inference.send', side_effect=lambda body, cfg: response(body)):
            run_plan(self.store, plan, execute=True)
        path = self.store.root/'inference/ledger.json'
        value = read_json(path)
        value['budgets']['test-budget']['prior_spend_cny'] = '9'
        path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(ContractError, 'LEDGER_CHECKSUM_MISMATCH'):
            run_plan(self.store, plan, execute=True)

    def test_parallel_run_lock_blocks_before_dispatch(self):
        plan = self.plan()
        path = self.store.root/'inference'
        path.mkdir()
        (path/'.runner.lock').write_text('test owner')
        with self.assertRaisesRegex(ContractError, 'INFERENCE_BUSY'):
            run_plan(self.store, plan, execute=True)

    def test_live_provider_requires_explicit_data_permission_and_key(self):
        plan = self.plan(provider='minimax', endpoint='https://api.minimaxi.com/v1/responses')
        with self.assertRaisesRegex(ContractError, 'NETWORK_NOT_AUTHORIZED'):
            run_plan(self.store, plan, execute=True)
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(ContractError, 'API_KEY_REQUIRED'):
                run_plan(self.store, plan, execute=True, allow_network=True)
        self.assertFalse((self.store.root/'inference/ledger.json').exists())

    def test_synthetic_provider_refuses_real_dataset(self):
        task = self.store.get(self.task)['payload']
        data = self.store.get(task['dataset'])['payload']
        real = self.store.put('dataset', dict(data, synthetic=False))
        other = self.store.put('prediction_task', dict(task, dataset=real), [real, self.rubric])
        with self.assertRaisesRegex(ContractError, 'SYNTHETIC_PROVIDER_REQUIRES_SYNTHETIC_DATA'):
            create_plan(self.store, other, config())

    def test_exact_student_evidence_rejects_ai_context_quote(self):
        plan = self.plan()
        def contaminated(body, cfg):
            result = response(body)
            pred = json.loads(result['output'][0]['content'][0]['text'])
            pred['evidence'] = 'AI answer only, absent from student question'
            result['output'][0]['content'][0]['text'] = json.dumps(pred)
            return result
        with patch('challenge.inference.send', side_effect=contaminated):
            result = run_plan(self.store, plan, execute=True)
        self.assertEqual(result['status'], 'validation_failed')
        self.assertEqual(result['error_code'], 'INVALID_PREDICTION')

    def test_parameters_and_short_followup_context_preserved(self):
        task = self.store.get(self.task)['payload']
        row = dict(turn_id='internal-id', question='为什么？', prior_context='Earlier student question and AI answer')
        body = responses_payload(task, 'rubric', row, 'row-000001')
        self.assertEqual(json.loads(body['input'][1]['content'])['prior_context'], row['prior_context'])
        self.assertNotIn('internal-id', json.dumps(body))
        self.assertEqual(body['max_output_tokens'], 512)
        self.assertFalse(body['store'])

    def test_configuration_rejects_secrets_arbitrary_endpoints_and_unbounded_attempts(self):
        for changes in ({'api_key':'private'}, {'max_attempts':0}, {'max_attempts':20},
                        {'budget_cny':'NaN'}, {'input_cny_per_million':'-1'},
                        {'provider':'minimax','endpoint':'https://unapproved.example/v1/responses'}):
            with self.subTest(changes=list(changes)):
                with self.assertRaises(ContractError): self.plan(**changes)

    def test_cli_plans_offline_and_executes_synthetic(self):
        path = self.store.root/'config.json'
        path.write_text(json.dumps(config()), encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = main(['--root',str(self.store.root),'prediction-plan','--task',self.task,'--config',str(path)])
        self.assertEqual(code, 0)
        plan = json.loads(out.getvalue())['result']['plan']
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = main(['--root',str(self.store.root),'prediction-run','--plan',plan,'--execute'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())['result']['status'], 'completed')


class DecoderTests(unittest.TestCase):
    def test_echoed_credential_is_rejected_before_response_can_be_persisted(self):
        cfg = config(provider='minimax', endpoint='https://api.minimaxi.com/v1/responses')
        placeholder = 'synthetic-secret-placeholder'
        raw = io.BytesIO(json.dumps({'echo':placeholder}).encode())
        with patch.dict('os.environ', {'BLOOMUP_MINIMAX_API_KEY':placeholder}):
            with patch('urllib.request.build_opener') as opener:
                opener.return_value.open.return_value = raw
                with self.assertRaisesRegex(ContractError, 'CREDENTIAL_ECHO_REJECTED'):
                    send({'model':'test'}, cfg)

    def test_single_complete_fence_allowed_no_repairs(self):
        self.assertEqual(decode_prediction('```json\n{"label":null}\n```'), {'label':None})
        for text in ('prefix {"label":1}', '{"label":1,"label":2}', '{"x":NaN}', '{"x":1e999}', '[]'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError): decode_prediction(text)

    def test_minimax_transport_preserves_payload_and_bounds_response(self):
        cfg = config(provider='minimax', endpoint='https://api.minimaxi.com/v1/responses')
        body = {'model':'synthetic-model', 'input':[], 'store':False}
        raw = io.BytesIO(b'{"usage":{"input_tokens":1,"output_tokens":2}}')
        with patch.dict('os.environ', {'BLOOMUP_MINIMAX_API_KEY':'test-placeholder'}):
            with patch('urllib.request.build_opener') as opener:
                opener.return_value.open.return_value = raw
                value = send(body, cfg)
                request = opener.return_value.open.call_args.args[0]
                self.assertEqual(json.loads(request.data), body)
                self.assertEqual(request.full_url, cfg['endpoint'])
                self.assertEqual(value['usage']['input_tokens'], 1)
        with self.assertRaisesRegex(ValueError, 'REDIRECT_BLOCKED'):
            _NoRedirect().redirect_request(None, None, 302, '', {}, 'https://unapproved.example')


if __name__ == '__main__':
    unittest.main()
