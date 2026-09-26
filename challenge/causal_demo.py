"""Synthetic causal fixtures and finite Monte Carlo diagnostics; never real evidence."""

from pathlib import Path
import random
import statistics

from .causal import _fit, _bootstrap
from .causal_workflow import run_causal_workflow
from .contracts import read_json, require


def synthetic_panel(effect=5, seed=None, trend_violation=0, term='synthetic'):
    protocol = read_json(Path(__file__).resolve().parents[1]/'configs/causal.protocol.template.json')
    protocol.update(term=term, treatment_contrast='SYNTHETIC AI support versus conventional instruction',
                    frozen_by='SYNTHETIC-ONLY',
                    design_evidence={k:'Synthetic data-generating mechanism only' for k in protocol['design_evidence']},
                    assumptions={k:dict(status='assumed',rationale='Synthetic assumption; violations separately simulated')
                                 for k in protocol['assumptions']})
    protocol['outcome'].update(name='Synthetic independent test',independent=True,comparable=True)
    rng = random.Random(seed)
    rows = []
    for d in (0,1):
        for category,count in [('low',24 if d == 0 else 12),('high',12 if d == 0 else 24)]:
            for j in range(count):
                pre = 30 if category == 'low' else 60
                trend = 2 if category == 'low' else 8
                cluster_noise = (-1 if j % 2 else 1) if seed is None else rng.gauss(0,2)
                for member in range(2):
                    noise = 0 if seed is None else rng.gauss(0,.5)
                    rows.append(dict(student_key=f'SYNTHETIC-{d}-{category}-{j}-{member}',
                                     cluster_id=f'SYNTHETIC-CLUSTER-{d}-{category}-{j}',term=term,treated=d,
                                     pre_score=pre,post_score=pre+trend+d*(effect+trend_violation)+cluster_noise+noise,
                                     baseline_time='2025-09-20T00:00:00+00:00',
                                     covariates_time='2025-09-20T00:00:00+00:00',
                                     treatment_time=protocol['time_zero'] if d else None,
                                     followup_time='2025-11-03T00:00:00+00:00',covariates={'foundation':category}))
    return dict(schema_version='causal-panel-v1',synthetic=True,rows=rows),protocol


def run_causal_demo(store):
    results = []
    for term,effect in [('synthetic_fall',5),('synthetic_spring',0)]:
        document,protocol = synthetic_panel(effect=effect,term=term)
        run = run_causal_workflow(store,document,protocol,replicates=300)
        results.append(dict(run,term=term,known_effect=effect))
    return dict(synthetic=True,studies=results,note='Separate synthetic studies, not pooled semesters or real effects.')


def validate_causal_simulation(store, simulations=100, replicates=200, seed=2045):
    require(type(simulations) is int and 20 <= simulations <= 1000, 'INVALID_SIMULATION_COUNT')
    require(type(replicates) is int and 100 <= replicates <= 1000 and type(seed) is int, 'INVALID_SIMULATION_CONFIG')
    results = []
    # Same random streams isolate changes in the imposed causal effect or trend violation.
    for name,effect,violation in [('null',0,0),('positive',5,0),('violated_parallel_trends',0,4)]:
        estimates,naive = [],[]
        covered = rejected = valid = 0
        for iteration in range(simulations):
            document,protocol = synthetic_panel(effect,seed+iteration,violation)
            fit = _fit(document['rows'],protocol['covariates'])
            inference = _bootstrap(document['rows'],protocol['covariates'],fit['estimate'],
                                   seed+10000+iteration,replicates,protocol['min_clusters_per_arm'])
            estimates.append(fit['estimate'])
            naive.append(fit['unadjusted_did'])
            if inference['ci95'] is not None:
                valid += 1
                covered += inference['ci95'][0] <= effect <= inference['ci95'][1]
                rejected += inference['p_value'] < .05
        coverage = covered/valid if valid else None
        results.append(dict(scenario=name,true_effect=effect,imposed_differential_trend=violation,
                            simulations=simulations,inference_available=valid,
                            mean_estimate=statistics.mean(estimates),bias=statistics.mean(estimates)-effect,
                            mean_unadjusted_did=statistics.mean(naive),
                            coverage_among_valid=coverage,coverage_mc_se=(coverage*(1-coverage)/valid)**.5 if valid else None,
                            rejection_rate_among_valid=rejected/valid if valid else None))
    return store.put('causal_validation',dict(synthetic=True,rows=results,seed=seed,replicates=replicates,
        simulations=simulations,
        limits=['Finite, seeded Monte Carlo diagnostics; not proof of nominal coverage in general.',
                'Coverage/rejection denominators are valid inferences; availability is reported separately.',
                'Two students share cluster noise; treatment-composition confounding varies across baseline strata.',
                'A false parallel-trends declaration cannot be detected from two-period data; the violation case is intentional.',
                'No real data, external inference, or empirical education-effect claims.']),
        config=dict(simulations=simulations,replicates=replicates,seed=seed))
