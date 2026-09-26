"""Pure, offline dialogue parsing, metric definitions and sample isolation."""

from collections import defaultdict
import hashlib
import html
import math
import random
import re


def parse_dialogue(value):
    """Extract candidate roles, never claim semantic role verification."""
    text = '' if value is None else str(value).strip()
    empty = {'turns': [], 'reason': ''}
    if not text:
        return dict(empty, status='missing')
    if re.fullmatch(r'https?://\S+', text):
        return dict(empty, status='url_only')
    # Only known HTML markup is removed; inequalities such as x < y survive.
    text = re.sub(r'<\s*/?\s*(?:p|div|br)\b[^>]*>', '\n', text, flags=re.I)
    text = re.sub(r'<\s*/?\s*(?:span|strong|b|i|u|em)\b[^>]*>', '', text, flags=re.I)
    text = html.unescape(text).strip()
    markers = []
    offset = 0
    fence = None
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        match_fence = re.match(r'(`{3,}|~{3,})', stripped)
        if match_fence:
            token = match_fence.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        elif fence is None:
            marker = re.match(r'\s*([QA])\s*[:：]', line)
            if marker:
                markers.append((marker.group(1), offset, offset + marker.end()))
        offset += len(line)
    if not markers:
        return dict(empty, status='needs_review', reason='no_role_markers')
    roles = [x[0] for x in markers]
    if (text[:markers[0][1]].strip() or roles[0] != 'Q' or roles[-1] != 'A'
            or any(a == b for a, b in zip(roles, roles[1:])) or fence is not None):
        return dict(empty, status='needs_review', reason='ambiguous_or_incomplete_roles')
    chunks = [text[item[2]:markers[i+1][1] if i+1 < len(markers) else len(text)].strip()
              for i, item in enumerate(markers)]
    if any(not chunk for chunk in chunks):
        return dict(empty, status='needs_review', reason='empty_role_content')
    turns = []
    for i in range(0, len(markers), 2):
        turns.append({'question': chunks[i], 'prior_context': text[:markers[i][1]].strip()})
    return {'status': 'format_ok_pending_review', 'reason': '', 'turns': turns}


def _simplex(values, length):
    if (len(values) != length or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                   or not math.isfinite(v) or v < 0 for v in values)
            or not math.isclose(sum(values), 1, abs_tol=1e-9)):
        raise ValueError(f'Expected {length} finite, nonnegative values summing to one')


def make_splits(turns, seed=20260925):
    """Assign whole student/long-exact-duplicate components to disjoint pools.

    Audit selection is probabilistic within term, with explicit two-stage weights.
    Risk selection is deliberately purposive and cannot estimate population quality.
    """
    students = sorted({row['student_key'] for row in turns})
    parents = {s: s for s in students}
    def find(s):
        while parents[s] != s:
            parents[s] = parents[parents[s]]
            s = parents[s]
        return s
    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parents[max(a, b)] = min(a, b)
    seen = {}
    for row in sorted(turns, key=lambda x: x['turn_id']):
        normal = re.sub(r'\W+', '', row['question']).casefold()
        if len(normal) < 36:
            continue
        key = hashlib.sha256(normal.encode()).hexdigest()
        if key in seen:
            union(row['student_key'], seen[key])
        else:
            seen[key] = row['student_key']
    components = defaultdict(list)
    for s in students:
        components[find(s)].append(s)
    rng = random.Random(seed)
    pool_by_student, component_by_student = {}, {}
    for root in sorted(components):
        draw = rng.random()
        pool = 'development' if draw < 0.15 else 'audit' if draw < 0.80 else 'risk'
        component = hashlib.sha256('|'.join(components[root]).encode()).hexdigest()[:16]
        for s in components[root]:
            pool_by_student[s], component_by_student[s] = pool, component
    candidates = defaultdict(list)
    for row in sorted(turns, key=lambda x: x['turn_id']):
        candidates[pool_by_student[row['student_key']], row['term']].append(row)
    selected = {p: [] for p in ['development', 'audit', 'risk']}
    selection_info = {}
    probabilities = {'development': 0.15, 'audit': 0.65, 'risk': 0.20}
    requests = {'development': 30, 'audit': 120, 'risk': 30}
    strata = []
    for (pool, term), rows in sorted(candidates.items()):
        rng.shuffle(rows)
        if pool == 'risk':
            # This selects candidates for human challenge review, never ground-truth labels.
            rows.sort(key=lambda x: sum(k in x['question'] for k in
                                       ['直接', '答案', '生成', '高级', '创造', '批判']) +
                      int(len(x['question']) < 12), reverse=True)
        chosen = rows[:requests[pool]]
        strata.append({'pool': pool, 'term': term, 'population_turns': len(rows),
                       'requested': requests[pool], 'selected': len(chosen)})
        for row in chosen:
            tid = row['turn_id']
            selected[pool].append(tid)
            conditional = len(chosen)/len(rows)
            selection_info[tid] = {
                'pool': pool, 'stratum': term,
                'component_id': component_by_student[row['student_key']],
                'stage1_probability': probabilities[pool],
                'stage2_probability': conditional if pool != 'risk' else None,
                'design_weight': 1/(probabilities[pool]*conditional) if pool != 'risk' else None,
            }
    return {'pool_by_student': pool_by_student, 'component_by_student': component_by_student,
            'selected': selected, 'selection_info': selection_info, 'strata': strata,
            'seed': seed, 'near_duplicate_semantic_review': 'pending'}
