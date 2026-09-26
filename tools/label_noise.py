"""Shared pure statistical helpers; no data files or student identifiers."""

from collections import Counter
import math


def assumed_neighbour_kernel(labels, error_mass=0.2):
    """Prevalence shapes neighbour allocation, never estimates the error mass."""
    if type(error_mass) not in (int, float) or not math.isfinite(error_mass) or not 0 <= error_mass <= 1:
        raise ValueError('Error mass must be a finite assumed probability')
    if not labels or any(type(level) is not int or not 1 <= level <= 6 for level in labels):
        raise ValueError('Kernel requires observed integer levels in 1..6')
    counts = Counter(labels)
    kernel = {}
    for level in range(1, 7):
        adjacent = [candidate for candidate in (level - 1, level + 1) if 1 <= candidate <= 6]
        weights = [counts[candidate] + 0.5 for candidate in adjacent]
        total = sum(weights)
        kernel[level] = tuple([(level, 1.0 - error_mass)] + [
            (candidate, error_mass * weight / total) for candidate, weight in zip(adjacent, weights)
        ])
    return kernel


def percentile(values, probability):
    if not values or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise ValueError('Percentile requires finite observations')
    if type(probability) not in (int, float) or not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError('Percentile probability must be in [0, 1]')
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (position - low) * (ordered[high] - ordered[low])
