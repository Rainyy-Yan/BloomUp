import json
from pathlib import Path
import tempfile
import unittest

from challenge.contracts import ContractError, fingerprint
from challenge.storage import ArtifactStore
from challenge.metrics import student_metrics


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ArtifactStore(Path(self.temp.name))

    def test_identity_is_deterministic_and_input_sensitive(self):
        one = self.store.put('dataset', {'rows': [1, 2]})
        self.assertEqual(one, self.store.put('dataset', {'rows': [1, 2]}))
        two = self.store.put('dataset', {'rows': [2, 1]})
        self.assertNotEqual(one, two)
        self.assertEqual(self.store.get(one, 'dataset')['payload']['rows'], [1, 2])

    def test_tampering_and_path_traversal_are_rejected(self):
        aid = self.store.put('dataset', {'rows': [1]})
        path = Path(self.temp.name)/'artifacts'/'dataset'/f'{aid}.json'
        value = json.loads(path.read_text(encoding='utf-8'))
        value['payload']['rows'] = [2]
        path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaises(ContractError): self.store.get(aid)
        with self.assertRaises(ContractError): self.store.get('../../private')

    def test_nan_and_unknown_dependency_rejected(self):
        with self.assertRaises(ValueError): fingerprint({'score': float('nan')})
        with self.assertRaises(ContractError): self.store.put('labels', {}, inputs=['missing'])

    def test_concurrent_writer_and_uncommitted_artifact_rejected(self):
        with self.store.writer():
            with self.assertRaisesRegex(ContractError,'WRITER_BUSY'): self.store.put('dataset',{'rows':[]})
        aid=self.store.put('dataset',{'rows':[]})
        value=json.loads(self.store.path(aid).read_text(encoding='utf-8'))
        value['status']='running'
        self.store.path(aid).write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaisesRegex(ContractError,'ARTIFACT_NOT_COMMITTED'): self.store.get(aid)

    def test_upstream_tampering_blocks_downstream_verification(self):
        source=self.store.put('dataset',{'rows':[1]})
        child=self.store.put('labels',{'rows':[]},[source])
        value=json.loads(self.store.path(source).read_text(encoding='utf-8'))
        value['payload']['rows']=[2]
        self.store.path(source).write_text(json.dumps(value),encoding='utf-8')
        with self.assertRaisesRegex(ContractError,'ARTIFACT_HASH_MISMATCH'): self.store.verify_tree(child)

    def test_deep_windows_workspace_uses_short_temporary_filename(self):
        root=Path(self.temp.name)/('x'*max(1,126-len(self.temp.name)-1))
        store=ArtifactStore(root)
        aid=store.put('prediction_task',{'rows':[]})
        self.assertEqual(store.get(aid)['payload'],{'rows':[]})


class EndpointTests(unittest.TestCase):
    def rows(self):
        return [{'turn_id': f't{i}', 'student_key': 's', 'record_id': 'r', 'turn_index': i,
                 'term': 'fall', 'agent_id': 'agent'} for i in range(1, 4)]

    def test_missing_first_is_not_replaced_by_second(self):
        result = student_metrics(self.rows(), {'t2': 2, 't3': 6}, [1/6]*6, [0.5, 0.3, 0.2])[0]
        self.assertIsNone(result['ctq'])
        self.assertIsNone(result['aiv'])
        self.assertAlmostEqual(result['identification_bounds'][1]-result['identification_bounds'][0], 30)
        self.assertIsNone(result['sampling_ci'])

    def test_missing_middle_preserves_original_endpoints(self):
        result = student_metrics(self.rows(), {'t1': 1, 't3': 6}, [1/6]*6, [0.5, 0.3, 0.2])[0]
        self.assertEqual(result['ctq'], 1)
        self.assertAlmostEqual(result['coverage'], 2/3)
        self.assertEqual(result['endpoint_pairs'], 1)


if __name__ == '__main__': unittest.main()
