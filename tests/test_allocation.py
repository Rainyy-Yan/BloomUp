import copy
import unittest

from challenge.allocation import solve_allocation, synthetic_allocation_spec, allocation_scenarios
from challenge.contracts import ContractError


class AllocationTests(unittest.TestCase):
    def test_sensitivity_keeps_infeasible_error_and_failure_scenarios(self):
        result={x['name']:x['result'] for x in allocation_scenarios()}
        self.assertEqual(len(result),7)
        self.assertEqual(result['higher_error']['status'],'infeasible')
        self.assertEqual(result['higher_na']['status'],'infeasible')
        self.assertLess(float(result['low_human_cost']['optimal']['cost_cny']),
                        float(result['high_human_cost']['optimal']['cost_cny']))

    def test_synthetic_solution_is_integer_reproducible_not_formal(self):
        spec = synthetic_allocation_spec()
        one, two = solve_allocation(spec), solve_allocation(copy.deepcopy(spec))
        self.assertEqual(one, two)
        self.assertEqual(one['status'], 'synthetic_scenario')
        self.assertFalse(one['independent_audit_verified'])
        self.assertIsNotNone(one['optimal'])
        for r in one['optimal']['strata']:
            self.assertEqual(r['m_ai'],r['frame_size'])
            self.assertIsInstance(r['n_double_review'],int)
            self.assertIsInstance(r['a_adjudicate'],int)
            self.assertLessEqual(r['n_double_review'],r['frame_size'])

    def test_no_pairs_blocks_instead_of_inventing_quality(self):
        spec=synthetic_allocation_spec()
        spec['strata'][0]['pilot_confusion']=[[0]*6 for _ in range(6)]
        spec['strata'][0]['pilot_prediction_na']=0
        result=solve_allocation(spec)
        self.assertEqual(result['status'],'blocked')
        self.assertIn('no_paired_pilot',result['blocked_reasons'])
        self.assertIsNone(result['optimal'])

    def test_missing_target_outputs_frontier_without_passing(self):
        spec=synthetic_allocation_spec();spec['targets']=None
        out=solve_allocation(spec)
        self.assertEqual(out['status'],'frontier_only')
        self.assertIsNone(out['optimal'])
        self.assertGreater(len(out['frontier']),0)

    def test_budget_and_quality_infeasible_are_distinct(self):
        spec=synthetic_allocation_spec();spec['budget_cny']='0.001'
        out=solve_allocation(spec)
        self.assertIn('budget_below_cheapest_feasible_quality',out['blocked_reasons'])
        spec=synthetic_allocation_spec();spec['targets']={'six':1.,'three':1.}
        out=solve_allocation(spec)
        self.assertIn('quality_unreachable_under_pilot_scenario',out['blocked_reasons'])

    def test_missing_class_reported_and_na_is_not_removed_from_denominator(self):
        spec=synthetic_allocation_spec()
        spec['strata'][0]['pilot_confusion'][4]=[0]*6
        spec['strata'][0]['pilot_prediction_na']=100
        out=solve_allocation(spec)
        self.assertIn(5,out['pilot_summary'][0]['unsupported_gold_classes'])
        self.assertLess(out['pilot_summary'][0]['prediction_coverage'],.5)
        self.assertFalse(out['formal_recommendation'])

    def test_tiny_stratum_census_and_ceiling_adjudication(self):
        spec=synthetic_allocation_spec()
        spec['strata']=spec['strata'][:1]
        spec['strata'][0].update(frame_size=1,min_reviews=1,disagreement_rate=.1)
        spec['targets']={'six':.5,'three':.5}
        result=solve_allocation(spec)['optimal']['strata'][0]
        self.assertEqual(result['precision_radius'],0)
        self.assertEqual(result['a_adjudicate'],1)
        self.assertEqual(float(result['cost_cny']),1.51)

    def test_three_tier_agreement_is_not_hot_binary(self):
        spec=synthetic_allocation_spec()
        matrix=[[0]*6 for _ in range(6)];matrix[2][3]=20
        spec['strata'][0].update(pilot_confusion=matrix,pilot_prediction_na=0)
        out=solve_allocation(spec)['pilot_summary'][0]
        self.assertEqual(out['six_agreement'],0)
        self.assertEqual(out['three_agreement'],1)

    def test_unknown_cost_blocks_real_recommendation(self):
        spec=synthetic_allocation_spec();spec['evidence_status']='real_pilot_estimate'
        spec['strata'][0]['double_review_unit_cny']=None
        out=solve_allocation(spec)
        self.assertEqual(out['status'],'blocked')
        self.assertIn('unknown_cost',out['blocked_reasons'])

    def test_enumeration_limit_is_not_false_infeasibility(self):
        spec=synthetic_allocation_spec();spec['max_combinations']=1
        out=solve_allocation(spec)
        self.assertEqual(out['status'],'search_limit')
        self.assertIsNone(out['optimal'])

    def test_rare_stratum_failure_cannot_be_hidden_by_pooled_quality(self):
        spec=synthetic_allocation_spec()
        spec['strata'][1]['pilot_prediction_na']=600
        out=solve_allocation(spec)
        self.assertEqual(out['status'],'infeasible')
        self.assertIsNone(out['optimal'])

    def test_invalid_counts_rejected(self):
        spec=synthetic_allocation_spec();spec['strata'][0]['frame_size']=True
        with self.assertRaises(ContractError):solve_allocation(spec)
        spec=synthetic_allocation_spec();spec['strata'][0]['pilot_confusion'][0][0]=-.1
        with self.assertRaises(ContractError):solve_allocation(spec)


if __name__=='__main__':unittest.main()
