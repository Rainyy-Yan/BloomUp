import json
from pathlib import Path
import tempfile
import unittest

from challenge.analysis import compute_metrics
from challenge.contracts import ContractError
from challenge.demo import run_demo
from challenge.storage import ArtifactStore
from challenge.uncertainty import label_sensitivity
from tools.label_noise import assumed_neighbour_kernel, percentile


class NoiseKernelTests(unittest.TestCase):
    def test_zero_noise_identity_and_endpoint_probability(self):
        kernel=assumed_neighbour_kernel([1,2,4,6],0)
        for level in range(1,7):
            self.assertEqual(dict(kernel[level])[level],1)
            self.assertAlmostEqual(sum(p for _,p in kernel[level]),1)
        kernel=assumed_neighbour_kernel([1,2,4,6],.2)
        self.assertEqual(set(dict(kernel[1])),{1,2})
        self.assertEqual(set(dict(kernel[6])),{5,6})
        self.assertAlmostEqual(dict(kernel[1])[2],.2)
        self.assertAlmostEqual(dict(kernel[6])[5],.2)

    def test_assumed_mass_is_not_empirical_accuracy(self):
        for labels in [[1]*100,[6]*100]:
            for level,row in assumed_neighbour_kernel(labels,.3).items():
                self.assertAlmostEqual(dict(row)[level],.7)
                self.assertTrue(all(abs(candidate-level)<=1 for candidate,_ in row))

    def test_invalid_labels_noise_and_percentiles_rejected(self):
        for levels,mass in [([True],.2),([0],.2),([1],float('nan')),([1],1.1),([1],True)]:
            with self.assertRaises(ValueError): assumed_neighbour_kernel(levels,mass)
        self.assertEqual(percentile([1,2,3,4],.5),2.5)
        with self.assertRaises(ValueError): percentile([], .5)


class LabelSensitivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.store=ArtifactStore(cls.temp.name)
        cls.demo=run_demo(cls.temp.name)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def run_scenario(self,metrics=None,masses=(0,.2)):
        aid=label_sensitivity(self.store,metrics or self.demo['metrics'],masses,replicates=100)
        return aid,self.store.get(aid)['payload']

    def test_reproducible_aggregate_only_zero_noise_bounds(self):
        aid,result=self.run_scenario()
        self.assertEqual(aid,self.run_scenario()[0])
        self.assertEqual(result['assumption_status'],'hypothetical_not_estimated')
        self.assertNotIn('SYNTHETIC-0',json.dumps(result))
        self.assertNotIn('question',json.dumps(result))
        row=result['rows'][0]
        self.assertEqual(row['error_mass'],0)
        for metric,summary in row['metrics'].items():
            if summary['observed'] is not None:
                self.assertEqual(summary['perturbation_interval_95'],[summary['observed']]*2)
        self.assertEqual(row['components'],6)
        self.assertEqual(result['claim_type'],'sensitivity')

    def test_missing_first_endpoint_not_imputed_by_noise(self):
        labels=self.store.get(self.demo['labels'])['payload']
        for row in labels['rows']:
            if row['turn_id'].endswith('-1'): row['label']=None
        labels_id=self.store.put('labels',labels,[self.demo['labels']])
        spec=self.store.get(self.demo['metrics'])['payload']['spec']
        metrics=compute_metrics(self.store,labels_id,spec)
        _,result=self.run_scenario(metrics)
        for row in result['rows']:
            for key in ['ctq','aiv']:
                self.assertIsNone(row['metrics'][key]['observed'])
                self.assertIsNone(row['metrics'][key]['perturbation_interval_95'])

    def test_invalid_configuration_and_empty_labels(self):
        with self.assertRaises(ContractError): self.run_scenario(masses=(.2,.2))
        with self.assertRaises(ContractError): label_sensitivity(self.store,self.demo['metrics'],replicates=1)
        labels=self.store.get(self.demo['labels'])['payload']
        for row in labels['rows']: row['label']=None
        labels_id=self.store.put('labels',labels,[self.demo['labels']])
        metrics=compute_metrics(self.store,labels_id,self.store.get(self.demo['metrics'])['payload']['spec'])
        with self.assertRaisesRegex(ContractError,'NO_VALID_LABELS'): self.run_scenario(metrics)

    def test_correlated_students_are_one_resampling_unit(self):
        data=self.store.get(self.demo['dataset'])['payload']
        data['sampling']['component_by_student']={key:'one-component' for key in data['sampling']['component_by_student']}
        dataset=self.store.put('dataset',data)
        labels=self.store.get(self.demo['labels'])['payload']
        labels['dataset']=dataset
        labels_id=self.store.put('labels',labels,[dataset])
        metrics=compute_metrics(self.store,labels_id,self.store.get(self.demo['metrics'])['payload']['spec'])
        _,result=self.run_scenario(metrics)
        for row in result['rows']:
            self.assertEqual(row['components'],1)
            self.assertTrue(all(value['joint_resampling_interval_95'] is None for value in row['metrics'].values()))
            self.assertIsNotNone(row['metrics']['abl']['perturbation_interval_95'])


if __name__=='__main__': unittest.main()
