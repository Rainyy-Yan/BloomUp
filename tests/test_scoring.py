import copy
import math
import random
import unittest

from challenge.scoring import (normalized_dhi, transition_quality, breadth_score,
                               aggregate_indicators, weight_stability_bound, feature_stability_bound,
                               validate_metric_spec)


def spec_v2():
    return {'formula_version':'aiv-v2','reference_distribution':[.1,.2,.25,.25,.15,.05],
            'reference_status':'provisional','reference_rationale':'Synthetic teaching target, not empirical evidence.',
            'utilities':[0,.2,.4,.6,.8,1], 'dhi_mode':'symmetric',
            'shortage_penalties':[1,1,1,1,1,1], 'excess_penalties':[1,1,1,1,1,1],
            'agent_catalog':['a','b','c'], 'agent_catalog_status':'provisional',
            'weights':[.5,.3,.15,.05], 'aggregation':{'kind':'linear'}}


class MathematicalProperties(unittest.TestCase):
    def setUp(self):
        self.q=[.1,.2,.25,.25,.15,.05]
        self.u=[0,.2,.4,.6,.8,1]
        self.w=[.5,.3,.15,.05]

    def test_normalized_dhi_target_and_farthest_vertex(self):
        self.assertAlmostEqual(normalized_dhi(self.q,self.q),1)
        self.assertAlmostEqual(normalized_dhi([0,0,0,0,0,1],self.q),0)
        self.assertAlmostEqual(normalized_dhi([1,0,0,0,0,0],self.q),1-.9/.95)

    def test_asymmetric_dhi_extreme_and_symmetric_reduction(self):
        shortage=[1,1,1,2,3,4]
        excess=[4,3,2,1,1,1]
        vertices=[[int(i==j) for i in range(6)] for j in range(6)]
        values=[normalized_dhi(p,self.q,shortage,excess) for p in vertices]
        self.assertAlmostEqual(min(values),0)
        self.assertTrue(all(0<=v<=1 for v in values))
        for p in vertices:
            self.assertAlmostEqual(normalized_dhi(p,self.q,[1]*6,[1]*6),normalized_dhi(p,self.q))

    def test_distance_from_target_monotonic_and_penalty_scale_invariant(self):
        vertex=[1,0,0,0,0,0]
        scores=[]
        for t in [0,.2,.5,1]:
            p=[(1-t)*q+t*v for q,v in zip(self.q,vertex)]
            scores.append(normalized_dhi(p,self.q,[1,2,3,4,5,6],[6,5,4,3,2,1]))
        self.assertEqual(scores,sorted(scores,reverse=True))
        self.assertAlmostEqual(scores[-1],normalized_dhi(vertex,self.q,[10,20,30,40,50,60],[60,50,40,30,20,10]))

    def test_ctq_direction_endpoints_and_partition_recomposition(self):
        self.assertEqual(transition_quality([1,6],self.u),1)
        self.assertEqual(transition_quality([6,1],self.u),0)
        self.assertEqual(transition_quality([2,6,2],self.u),.5)
        full=transition_quality([1,4,2,6],self.u)
        pieces=[transition_quality([1,4],self.u),transition_quality([4,2],self.u),transition_quality([2,6],self.u)]
        self.assertAlmostEqual(full,.5+sum(p-.5 for p in pieces))
        self.assertEqual(transition_quality([1,None,6],self.u),1)
        self.assertIsNone(transition_quality([None,2,6],self.u))
        self.assertIsNone(transition_quality([3],self.u))
        self.assertAlmostEqual(transition_quality([2,5],[0,.1,.3,.5,.9,1]),.9)

    def test_breadth_endpoints_and_diminishing_returns(self):
        values=[breadth_score(k,5) for k in range(6)]
        self.assertEqual(values[0],0)
        self.assertEqual(values[-1],1)
        increments=[b-a for a,b in zip(values,values[1:])]
        self.assertEqual(increments,sorted(increments,reverse=True))
        for k,m in [(6,5),(-1,5),(1,0),(True,5)]:
            with self.assertRaises(ValueError): breadth_score(k,m)

    def test_missing_features_produce_conditional_bounds_without_reweighting(self):
        out=aggregate_indicators([.6,None,.8,None],self.w,{'kind':'linear'})
        self.assertIsNone(out['aiv'])
        self.assertAlmostEqual(out['identification_bounds'][0],42)
        self.assertAlmostEqual(out['identification_bounds'][1],77)
        zero_weight=aggregate_indicators([.6,None,.8,None],[.6,0,.4,0],{'kind':'linear'})
        self.assertAlmostEqual(zero_weight['aiv'],68)
        self.assertEqual(aggregate_indicators([None]*4,self.w,{'kind':'linear'})['identification_bounds'],[0,100])

    def test_composites_monotone_bounded_and_auditable(self):
        rng=random.Random(71)
        for aggregation in [{'kind':'linear'},{'kind':'concave','rho':1},{'kind':'concave','rho':1e-12}]:
            self.assertAlmostEqual(aggregate_indicators([0]*4,self.w,aggregation)['aiv'],0)
            self.assertAlmostEqual(aggregate_indicators([1]*4,self.w,aggregation)['aiv'],100)
            for _ in range(50):
                x=[rng.random() for _ in range(4)]
                y=[v+(1-v)*rng.random() for v in x]
                left=aggregate_indicators(x,self.w,aggregation)
                right=aggregate_indicators(y,self.w,aggregation)
                self.assertLessEqual(left['aiv'],right['aiv']+1e-10)
                self.assertAlmostEqual(sum(left['contributions']),left['aiv'])
                bound=feature_stability_bound([b-a for a,b in zip(x,y)],self.w,aggregation)
                self.assertLessEqual(abs(right['aiv']-left['aiv']),bound+1e-9)

    def test_weight_bound_and_concavity(self):
        x=[.1,.7,.9,.3]
        other=[.3,.3,.3,.1]
        delta=abs(aggregate_indicators(x,self.w,{'kind':'linear'})['aiv']-aggregate_indicators(x,other,{'kind':'linear'})['aiv'])
        self.assertLessEqual(delta,weight_stability_bound(self.w,other))
        values=[aggregate_indicators([v]*4,self.w,{'kind':'concave','rho':1})['aiv'] for v in [0,.25,.5,.75,1]]
        increments=[b-a for a,b in zip(values,values[1:])]
        self.assertEqual(increments,sorted(increments,reverse=True))

    def test_invalid_domains_fail_instead_of_clipping(self):
        for q in [[1,0,0,0,0,0],[.2]*6]:
            with self.assertRaises(ValueError): normalized_dhi(self.q,q)
        with self.assertRaises(ValueError): normalized_dhi(self.q,self.q,[0]*6,[1]*6)
        with self.assertRaises(ValueError): aggregate_indicators([1,1,math.nan,1],self.w,{'kind':'linear'})
        with self.assertRaises(ValueError): aggregate_indicators([1]*4,self.w,{'kind':'concave','rho':0})
        with self.assertRaises(ValueError): transition_quality([True,6],self.u)

    def test_versioned_spec_rejects_invalid_catalog_and_unjustified_reference(self):
        spec=spec_v2()
        self.assertEqual(validate_metric_spec(spec),'aiv-v2')
        legacy={'reference_distribution':self.q,'weights':[.5,.3,.2],'reference_status':'provisional'}
        self.assertEqual(validate_metric_spec(legacy),'legacy-v1')
        for key,value in [('agent_catalog',['a','a']),('utilities',[0,.5,.4,.6,.8,1]),('reference_rationale','')]:
            bad=copy.deepcopy(spec)
            bad[key]=value
            with self.assertRaises(ValueError): validate_metric_spec(bad)


if __name__=='__main__': unittest.main()
