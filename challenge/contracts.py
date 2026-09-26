"""Versioned contract utilities for the offline analysis application."""

import hashlib
import json
import math
from pathlib import Path


class ContractError(ValueError):
    pass


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def require(condition, code, detail=''):
    if not condition:
        raise ContractError(f'{code}: {detail}')


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'DUPLICATE_JSON_KEY', key)
        result[key] = value
    return result


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=_pairs,
                          parse_constant=lambda x: (_ for _ in ()).throw(ContractError('NONFINITE_JSON')))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f'INVALID_JSON: {Path(path).name}') from exc


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def turn_hash(turn):
    return fingerprint({key: turn[key] for key in ['turn_id', 'record_id', 'student_key', 'turn_index', 'question', 'prior_context']})


def label_check(label, evidence, question, insufficient, reason):
    require(type(insufficient) is bool, 'INVALID_INSUFFICIENT_FLAG')
    require(isinstance(evidence, str), 'INVALID_EVIDENCE_TYPE')
    require(isinstance(reason, str) and reason.strip(), 'REASON_REQUIRED')
    if label is None:
        require(insufficient, 'NA_REQUIRES_INSUFFICIENT')
    else:
        require(type(label) is int and 1 <= label <= 6, 'INVALID_LABEL')
        require(not insufficient, 'LABEL_WITH_INSUFFICIENT_EVIDENCE')
        require(isinstance(evidence, str) and bool(evidence.strip()) and evidence in question, 'EVIDENCE_NOT_IN_QUESTION')


def finite_number(value, low=None, high=None):
    require(type(value) in (int, float) and math.isfinite(value), 'INVALID_NUMBER')
    require(low is None or value >= low, 'NUMBER_OUT_OF_RANGE')
    require(high is None or value <= high, 'NUMBER_OUT_OF_RANGE')
    return value
