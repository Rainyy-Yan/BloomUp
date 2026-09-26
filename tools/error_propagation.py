"""T4.5.1: aggregate-only adjacent-label sensitivity and student bootstrap.

This is NOT an estimated annotator confusion matrix. One human label per item
cannot identify P(predicted level | true level). The frozen 20% error mass is an
explicit scenario; bootstrap label prevalence only allocates it to neighbours.
"""

from __future__ import annotations

import csv
import hashlib
import math
import random
import statistics
from collections import defaultdict

if __package__:
    from .build_student_mapping import ROOT, build_mapping
    from .label_noise import assumed_neighbour_kernel, percentile
else:
    from build_student_mapping import ROOT, build_mapping
    from label_noise import assumed_neighbour_kernel, percentile


DATA = ROOT / "data"
OUT = DATA / "error_propagation_results.csv"
REPS = 1000
SEED = 2045
ADJACENT_ERROR_MASS = 0.20  # Assumed scenario, NOT learned from these labels.
SEMESTERS = ("2025_fall", "2026_spring")
FIELDS = (
    "semester", "n_records_abl", "mean_ABL", "ci_lo_ABL", "ci_hi_ABL",
    "mean_HOT_pct", "ci_lo_HOT", "ci_hi_HOT", "mean_CTQ", "ci_lo_CTQ",
    "ci_hi_CTQ", "top_quartile_stability", "ranking_flip_rate",
)


def read_rows(name: str, expected: int) -> list[dict[str, str]]:
    with (DATA / name).open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != expected:
        raise ValueError(f"{name}: expected {expected} rows, got {len(rows)}")
    return rows


def level_from(row: dict[str, str]) -> int:
    level = int(row["gt_level"])
    conf = float(row["gt_confidence"])
    if not (1 <= level <= 6 and math.isfinite(conf) and 0 <= conf <= 1):
        raise ValueError("Incomplete or out-of-range human annotation")
    if not row["gt_rationale"].strip() or not row["question_text"].strip():
        raise ValueError("Missing annotation rationale or student question")
    return level


def load_inputs() -> tuple[dict, dict]:
    # build_mapping also reads qa_text.csv and the original Excel workbooks;
    # anonymized student IDs and question text are never written or printed.
    mapping = build_mapping()
    records: dict[str, dict[str, list[int]]] = {
        semester: defaultdict(list) for semester in SEMESTERS
    }
    turns: dict[str, dict[str, dict[str, list[tuple[int, int]]]]] = {
        semester: defaultdict(dict) for semester in SEMESTERS
    }
    record_rows = read_rows("ground_truth_stratified_sample.csv", 50)
    record_rows += read_rows("ground_truth_stratified_sample_v2.csv", 50)
    seen_qids: set[str] = set()
    for row in record_rows:
        qid = row["qid"].strip()
        semester = row["semester"].strip()
        if qid in seen_qids or semester not in SEMESTERS or qid not in mapping:
            raise ValueError("Duplicate, unknown, or invalid record qid")
        seen_qids.add(qid)
        if mapping[qid].semester != semester:
            raise ValueError("Record semester does not match source")
        records[semester][mapping[qid].student_key].append(level_from(row))
    if {s: sum(map(len, records[s].values())) for s in SEMESTERS} != {
        "2025_fall": 50, "2026_spring": 50
    }:
        raise ValueError("Expected 50 record-level labels in each semester")

    seen_turns: set[tuple[str, int]] = set()
    for row in read_rows("ground_truth_turn_sample.csv", 55):
        cid = row["conversation_id"].strip()
        semester = row["semester"].strip()
        index = int(row["turn_index"])
        qid = cid.replace("-c", "-r", 1)
        if (cid, index) in seen_turns or semester not in SEMESTERS or qid not in mapping:
            raise ValueError("Duplicate, unknown, or invalid turn key")
        seen_turns.add((cid, index))
        if mapping[qid].semester != semester:
            raise ValueError("Turn semester does not match source")
        student = mapping[qid].student_key
        turns[semester][student].setdefault(cid, []).append((index, level_from(row)))
    if len({cid for term in SEMESTERS for student in turns[term].values() for cid in student}) != 15:
        raise ValueError("Expected 15 distinct multi-turn conversations")
    for semester in SEMESTERS:
        for student_dialogues in turns[semester].values():
            for cid, ordered in student_dialogues.items():
                ordered.sort()
                if len(ordered) < 2 or [i for i, _ in ordered] != list(range(1, len(ordered) + 1)):
                    raise ValueError("Incomplete turn sequence")
                qid = cid.replace("-c", "-r", 1)
                if mapping[qid].n_records != len(ordered):
                    raise ValueError("Turn count differs from source record")
    return records, turns


def neighbour_kernel(labels: list[int]) -> dict[int, tuple[tuple[int, float], ...]]:
    """Prevalence-shaped ASSUMED kernel; frequencies are not transitions."""
    return assumed_neighbour_kernel(labels, ADJACENT_ERROR_MASS)


def perturb(level: int, kernel: dict, rng: random.Random) -> int:
    draw = rng.random()
    total = 0.0
    for candidate, probability in kernel[level]:
        total += probability
        if draw < total:
            return candidate
    return kernel[level][-1][0]


def record_metrics(by_student: dict[str, list[int]]) -> tuple[dict[str, float], dict[str, float]]:
    abl = {student: statistics.mean(labels) for student, labels in by_student.items()}
    hot = {student: 100 * statistics.mean(level >= 4 for level in labels)
           for student, labels in by_student.items()}
    return abl, hot


def ctq_metrics(by_student: dict[str, dict[str, list[tuple[int, int]]]]) -> dict[str, float]:
    result = {}
    for student, dialogues in by_student.items():
        values = [0.5 + (ordered[-1][1] - ordered[0][1]) / 10
                  for ordered in dialogues.values()]
        result[student] = statistics.mean(values)
    return result


def ranking(abl: dict[str, float]) -> set[str]:
    """Fixed-size top quartile. Hash only breaks ABL ties; no ID leaves memory."""
    k = math.ceil(len(abl) / 4)
    ranked = sorted(abl, key=lambda student: (
        -abl[student], hashlib.sha256(student.encode("utf-8")).digest()
    ))
    return set(ranked[:k])


def scenario(records: dict, turns: dict, semester: str, rng: random.Random) -> tuple[float, float, float, float]:
    rec = records[semester]
    trn = turns[semester]
    rec_students = sorted(rec)
    turn_students = sorted(trn)
    rec_draw = rng.choices(rec_students, k=len(rec_students))
    turn_draw = rng.choices(turn_students, k=len(turn_students))
    rec_kernel = neighbour_kernel([level for student in rec_draw for level in rec[student]])
    turn_kernel = neighbour_kernel([
        level for student in turn_draw for ordered in trn[student].values() for _, level in ordered
    ])
    perturbed_rec = {
        student: [perturb(level, rec_kernel, rng) for level in levels]
        for student, levels in rec.items()
    }
    perturbed_turns = {
        student: {cid: [(index, perturb(level, turn_kernel, rng)) for index, level in ordered]
                  for cid, ordered in dialogues.items()}
        for student, dialogues in trn.items()
    }
    abl, hot = record_metrics(perturbed_rec)
    ctq = ctq_metrics(perturbed_turns)
    baseline_top = ranking(record_metrics(rec)[0])
    stable = len(baseline_top & ranking(abl)) / len(baseline_top)
    return (
        statistics.mean(abl[student] for student in rec_draw),
        statistics.mean(hot[student] for student in rec_draw),
        statistics.mean(ctq[student] for student in turn_draw),
        stable,
    )


def main() -> None:
    records, turns = load_inputs()
    rng = random.Random(SEED)
    # One Monte Carlo round evaluates both semesters; exactly REPS rounds.
    series_by_semester = {semester: [[], [], [], []] for semester in SEMESTERS}
    for _ in range(REPS):
        for semester in SEMESTERS:
            values = scenario(records, turns, semester, rng)
            for series, value in zip(series_by_semester[semester], values):
                series.append(value)
    output = []
    for semester in SEMESTERS:
        base_abl, base_hot = record_metrics(records[semester])
        base_ctq = ctq_metrics(turns[semester])
        series = series_by_semester[semester]
        stability = statistics.mean(series[3])
        row = {
            "semester": semester,
            "n_records_abl": sum(map(len, records[semester].values())),
            "mean_ABL": statistics.mean(base_abl.values()),
            "ci_lo_ABL": percentile(list(series[0]), 0.025),
            "ci_hi_ABL": percentile(list(series[0]), 0.975),
            "mean_HOT_pct": statistics.mean(base_hot.values()),
            "ci_lo_HOT": percentile(list(series[1]), 0.025),
            "ci_hi_HOT": percentile(list(series[1]), 0.975),
            "mean_CTQ": statistics.mean(base_ctq.values()),
            "ci_lo_CTQ": percentile(list(series[2]), 0.025),
            "ci_hi_CTQ": percentile(list(series[2]), 0.975),
            "top_quartile_stability": stability,
            "ranking_flip_rate": 1.0 - stability,
        }
        output.append(row)
    with OUT.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(output)
    print(f"Wrote {len(output)} semester-level rows; {REPS} joint scenario/bootstrap rounds.")
    for row in output:
        print(f"{row['semester']}: ABL={row['mean_ABL']:.4f} [{row['ci_lo_ABL']:.4f},{row['ci_hi_ABL']:.4f}], "
              f"HOT%={row['mean_HOT_pct']:.2f} [{row['ci_lo_HOT']:.2f},{row['ci_hi_HOT']:.2f}], "
              f"CTQ={row['mean_CTQ']:.4f} [{row['ci_lo_CTQ']:.4f},{row['ci_hi_CTQ']:.4f}], "
              f"top stability={row['top_quartile_stability']:.4f}, "
              f"flip={row['ranking_flip_rate']:.4f}")


if __name__ == "__main__":
    main()
