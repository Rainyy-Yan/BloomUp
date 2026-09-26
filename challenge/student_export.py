"""Explicit local-only student tables; no publication or formal-score promotion."""

from collections import defaultdict
import math
import re
import uuid

from .comparison import _ranks, scheme_definitions
from .contracts import file_hash, fingerprint, read_json, require
from .pipeline import write_csv
from .scoring import INDICATORS, aggregate_indicators


def apply_roster(metric, metrics_id, roster):
    """Use an explicitly supplied anonymous roster without inventing observations."""
    require(isinstance(roster,dict) and set(roster)=={'metrics_id','scope_status','source_ref','students'},
            'INVALID_ROSTER')
    require(roster['metrics_id']==metrics_id,'ROSTER_METRICS_MISMATCH')
    require(roster['scope_status'] in ('provisional','confirmed'),'INVALID_ROSTER_SCOPE')
    require(isinstance(roster['source_ref'],str) and roster['source_ref'].strip(),'ROSTER_SOURCE_REQUIRED')
    require(isinstance(roster['students'],list),'INVALID_ROSTER_STUDENTS')
    terms=set(metric.get('population_counts',{})) | {r['term'] for r in metric['rows']}
    seen=set()
    for row in roster['students']:
        require(isinstance(row,dict) and set(row)=={'term','anonymous_id'},'INVALID_ROSTER_ROW')
        require(isinstance(row['term'],str) and row['term'] in terms,'INVALID_ROSTER_TERM')
        require(isinstance(row['anonymous_id'],str) and
                re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}',row['anonymous_id']), 'INVALID_ROSTER_ID')
        identity=(row['term'],row['anonymous_id'])
        require(identity not in seen,'DUPLICATE_ROSTER_STUDENT')
        seen.add(identity)
    observed={(r['term'],r['student_key']) for r in metric['rows']}
    require(observed <= seen,'ROSTER_MISSING_METRIC_STUDENT')
    rows=list(metric['rows'])
    for term,student in sorted(seen-observed):
        rows.append(dict(term=term,student_key=student,candidate_turns=None,labeled_turns=None,
                         missing_reasons=['no_metric_row_for_roster_member'],**{k:None for k in INDICATORS}))
    return dict(metric,rows=rows,population_counts={term:sum(t==term for t,_ in seen) for term in terms})


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


def export_students(store, metrics_id, enabled=False, roster_file=None):
    require(enabled is True, 'STUDENT_EXPORT_NOT_ENABLED')
    store.verify_tree(metrics_id)
    metric = store.get(metrics_id, 'metrics')['payload']
    roster_id=None
    roster_scope=None
    if roster_file is not None:
        roster=read_json(roster_file)
        metric=apply_roster(metric,metrics_id,roster)
        roster_id=store.put('student_roster',roster,[metrics_id])
        roster_scope=roster['scope_status']
    rows = build_rows(metric, metrics_id)
    require(rows, 'EMPTY_STUDENT_EXPORT')
    for row in rows:
        row.update(roster_artifact_id=roster_id,roster_scope_status=roster_scope,
                   source_population_basis='explicit_roster' if roster_id else 'metrics_population_counts')
    path = store.root/'exports'/uuid.uuid4().hex/'student-results.csv'
    write_csv(path, rows, list(rows[0]))
    artifact = store.put('student_export', dict(metrics=metrics_id, relative_path=path.relative_to(store.root).as_posix(),
                                               sha256=file_hash(path), rows=len(rows),
                                               destination='controlled_local_only', published=False,
                                               formula_version='aiv-v2', synthetic=metric['synthetic'],
                                               rank_scope='within_term_common_four_indicator_complete_cohort',
                                               roster=roster_id,
                                               roster_only_students='included_with_missing_metrics' if roster_id else 'aggregate_only_not_individually_exported'),
                         [metrics_id]+([roster_id] if roster_id else []))
    return dict(artifact_id=artifact, file=str(path), rows=len(rows), visibility='internal', published=False)
