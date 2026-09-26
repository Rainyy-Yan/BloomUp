from pathlib import Path
import tempfile
import unittest

from challenge.contracts import ContractError
from challenge.probes import prepare_probes, summarize_probes
from challenge.storage import ArtifactStore


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ArtifactStore(self.tmp.name)
        project = Path(__file__).resolve().parents[1]
        self.task = prepare_probes(self.store, {'name':'synthetic-test','revision':'test',
                                             'parameters':{'max_output_tokens':512}},
                                   project/'prompts/认知预标注_v2_candidate.md')['task_id']

    def predictions(self, levels):
        task = self.store.get(self.task)['payload']
        rows = [{'turn_id':r['turn_id'],'label':level} for r,level in zip(task['rows'], levels)]
        return self.store.put('predictions', dict(task=self.task,dataset=task['dataset'],rows=rows), [self.task])

    def test_equal_labels_only_prove_fixture_invariance(self):
        result = summarize_probes(self.store, self.predictions([1]*8))
        self.assertEqual(result['status_counts'], {'PASS':4})
        self.assertFalse(result['independent_quality_verified'])
        self.assertFalse(result['student_data_used'])

    def test_changed_and_missing_labels_are_not_hidden(self):
        result = summarize_probes(self.store, self.predictions([1,6,None,None,1,1,2,2]))
        self.assertEqual(result['status_counts'], {'FAIL':1,'NA':1,'PASS':2})

    def test_incomplete_pair_is_rejected(self):
        with self.assertRaisesRegex(ContractError, 'PROBE_ROW_SET_MISMATCH'):
            summarize_probes(self.store, self.predictions([1]*7))

    def test_arbitrary_dataset_is_not_a_probe(self):
        task = self.store.get(self.task)['payload']
        data = self.store.get(task['dataset'])['payload']
        data['synthetic'] = False
        other = self.store.put('dataset', data)
        pred = self.store.put('predictions',dict(task=self.task,dataset=other,rows=[]),[self.task,other])
        with self.assertRaisesRegex(ContractError, 'PROBE_DATASET_MISMATCH'):
            summarize_probes(self.store, pred)
