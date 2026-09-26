"""Explicit local-only student tables; no publication or formal-score promotion."""

from collections import defaultdict
import math
import uuid

from .comparison import _ranks, scheme_definitions
from .contracts import file_hash, fingerprint, require
from .pipeline import write_csv
from .scoring import INDICATORS, aggregate_indicators


def build_rows(metric, metrics_id):
    require(metric.get('formula_version') == 'aiv-v2', 'V2_EXPORT_REQUIRED')
    spec = metric['spec']
    scenarios = scheme_definitions(spec, include_sensitivity=False)
    terms = defaultdict(list)
    identities = set()
    for row in metric['rows']:
        key = (row['term'],row['student_key'])
        require(key not in identities, 'DUPLICATE_STUDENT')
        identities.add(key)
        terms[row['term']].append(row)
    output = []
    for term, candidates in sorted(terms.items()):
        eligible = [r for r in candidates if all(r[k] is not None for k in INDICATORS)]
        ids = {r['student_key']:i for i,r in enumerate(eligible)}
        for name, weights, aggregation in scenarios:
            scores = [aggregate_indicators([r[k] for k in INDICATORS], weights, aggregation)['aiv'] for r in eligible]
            ranks = [len(scores)-r for r in _ranks(scores)]
            cutoff = sorted(scores, reverse=True)[math.ceil(len(scores)/4)-1] if scores else None
            for row in candidates:
                features = [row[k] for k in INDICATORS]
                score = aggregate_indicators(features, weights, aggregation)
                index = ids.get(row['student_key'])
                missing = list(row.get('missing_reasons', []))
                missing += [f'missing_{k}' for k in INDICATORS if row[k] is None]
                bounds = score['identification_bounds']
                output.append(dict(
                    anonymous_id=row['student_key'], term=term, scenario=name,
                    dataset_id=metric['dataset'], labels_id=metric['labels'], metrics_id=metrics_id,
                    formula_version='aiv-v2', spec_hash=fingerprint(spec),
                    reference_status=spec['reference_status'], agent_catalog_status=spec['agent_catalog_status'],
                    scope_status=metric['scope_status'], result_status='synthetic' if metric['synthetic'] else 'exploratory',
                    **{k:row[k] for k in INDICATORS}, aiv=score['aiv'],
                    conditional_lower=bounds[0] if bounds else None,
                    conditional_upper=bounds[1] if bounds else None,
                    candidate_turns=row['candidate_turns'], labeled_turns=row['labeled_turns'],
                    label_coverage=row['labeled_turns']/row['candidate_turns'] if row['candidate_turns'] else None,
                    rank=ranks[index] if index is not None else None,
                    top_quartile=scores[index] >= cutoff if index is not None else None,
                    rank_rule='descending_average_exact_ties', top_quartile_rule='score_cutoff_includes_boundary_ties',
                    comparison_population=len(eligible), metric_population=len(candidates),
                    source_population=metric.get('population_counts', {}).get(term),
                    missing_reasons=';'.join(dict.fromkeys(missing)),
                    interpretation='Observed questioning demand; not ability, achievement or AI causal effect'))
    return output


def export_students(store, metrics_id, enabled=False):
    require(enabled is True, 'STUDENT_EXPORT_NOT_ENABLED')
    store.verify_tree(metrics_id)
    metric = store.get(metrics_id, 'metrics')['payload']
    rows = build_rows(metric, metrics_id)
    require(rows, 'EMPTY_STUDENT_EXPORT')
    path = store.root/'exports'/uuid.uuid4().hex/'student-results.csv'
    write_csv(path, rows, list(rows[0]))
    artifact = store.put('student_export', dict(metrics=metrics_id, relative_path=path.relative_to(store.root).as_posix(),
                                               sha256=file_hash(path), rows=len(rows),
                                               destination='controlled_local_only', published=False,
                                               formula_version='aiv-v2', synthetic=metric['synthetic'],
                                               rank_scope='within_term_common_four_indicator_complete_cohort',
                                               roster_only_students='aggregate_only_not_individually_exported'), [metrics_id])
    return dict(artifact_id=artifact, file=str(path), rows=len(rows), visibility='internal', published=False)
