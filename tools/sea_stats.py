"""Summarize JSON-lines water measurements from sea_live."""
import argparse
import json
import math
import statistics
from pathlib import Path


def correlation(a, b):
    ma, mb = statistics.mean(a), statistics.mean(b)
    da, db = [x - ma for x in a], [x - mb for x in b]
    denom = math.sqrt(sum(x*x for x in da) * sum(x*x for x in db))
    return sum(x*y for x, y in zip(da, db)) / denom if denom else 0.0


def summarize(rows):
    bands = list(zip(*(row['bands'] for row in rows)))
    center = [row['water_y'][2] for row in rows]
    amplitude = [max(row['water_y']) - min(row['water_y']) for row in rows]
    impulse = [row.get('impulse', 0.0) for row in rows]
    vocal = [row.get('vocal', 0.0) or 0.0 for row in rows]
    lifts = [row.get('water_level_lift', 0.0) or 0.0 for row in rows]
    jumps = [abs(b - a) for a, b in zip(center, center[1:])]
    vibration = [row.get('vibration', 0.0) or 0.0 for row in rows]
    onsets = [row.get('onset', 0.0) or 0.0 for row in rows]
    spectral_correlations = []
    for row in rows:
        spectrum = row.get('spectrum') or ()
        water = row.get('water_y17') or ()
        if len(spectrum) > 1 and len(water) == 17:
            # Remove the known modal vibration before comparing spatial shape.
            shape = [99.0 - y - row.get('vibration', 0.0) * (.75 + .25 * math.cos(i * math.tau / 16)) for i, y in enumerate(water)]
            interpolated = []
            for i in range(17):
                x = i * (len(spectrum)-1) / 16
                index = int(x); fraction = x-index
                interpolated.append(spectrum[index] * (1-fraction) + spectrum[min(index+1,len(spectrum)-1)] * fraction)
            spectral_correlations.append(correlation(shape, interpolated))
    fields = [row.get('water_y17') or row.get('water_y') for row in rows]
    fields = [list(map(float, field)) for field in fields]
    shifts, explained = [], []
    for previous, current in zip(fields, fields[1:]):
        n = min(len(previous), len(current))
        previous, current = previous[:n], current[:n]
        best_shift, best_error = 0, float('inf')
        for shift_units in range(-100, 101):
            shift = shift_units / 20.0
            pairs = []
            for i in range(n):
                source = i + shift
                left = math.floor(source)
                fraction = source - left
                if 0 <= left < n - 1:
                    predicted = previous[left] * (1.0 - fraction) + previous[left + 1] * fraction
                    pairs.append((predicted, current[i]))
            if pairs:
                error = sum((b - a) ** 2 for a, b in pairs) / len(pairs)
                if error < best_error:
                    best_error, best_shift = error, shift
        shifts.append(abs(best_shift) * 440.0 / max(1, n - 1))
        delta = [current[i] - previous[i] for i in range(n)]
        total = sum((x - statistics.mean(delta)) ** 2 for x in delta)
        explained.append(max(0.0, 1.0 - best_error / (total / max(1, n) + 1e-9)))
    # Subtract the logged vibration mode before estimating common shape motion.
    shape_fields = [[y + (row.get('vibration', 0.0) or 0.0) * (.75 + .25 * math.cos(i * math.tau / max(1, len(field)-1)))
                     for i, y in enumerate(field)] for row, field in zip(rows, fields)]
    means = [statistics.mean(field) for field in shape_fields]
    total_variance = sum(sum((x - statistics.mean(field)) ** 2 for x in field) for field in shape_fields) / max(1, len(shape_fields))
    common_variance = statistics.pvariance(means) if len(means) > 1 else 0.0
    cohesion = common_variance / (total_variance / max(1, len(fields[0])) + 1e-9)
    flat_fraction = statistics.mean(sum(abs(y - 101.0) < 1.5 for y in field) / len(field) for field in fields)
    curvature = []
    for field in fields:
        curvature.extend(abs(field[i - 1] - 2.0 * field[i] + field[i + 1]) for i in range(1, len(field) - 1))
    crest_counts = []
    for field in fields:
        crest_counts.append(sum(field[i] < field[i - 1] and field[i] <= field[i + 1] for i in range(1, len(field) - 1)))
    correlations = []
    for field in fields:
        centered = [x - statistics.mean(field) for x in field]
        variance = sum(x*x for x in centered)
        if not variance:
            continue
        for distance in range(1, len(centered)):
            corr = sum(centered[i] * centered[i + distance] for i in range(len(centered) - distance)) / variance
            if corr <= 0.5:
                correlations.append(distance / max(1, len(centered) - 1))
                break
    def stats(values):
        return {'min': min(values), 'max': max(values), 'std': statistics.pstdev(values)}
    return {'bands': [stats(band) for band in bands], 'center': stats(center),
            'amplitude_mean': statistics.mean(amplitude),
            'amplitude_p95': sorted(amplitude)[math.ceil(len(amplitude)*.95)-1],
            'spectral_shape_corr': statistics.median(spectral_correlations) if spectral_correlations else None,
            'vibration_onset_corr': correlation([abs(v) for v in vibration], onsets),
            'corr_low_amplitude': correlation(bands[0], amplitude),
            'corr_low_center_rise': correlation(bands[0], [-y for y in center]),
            'corr_vocal_center_rise': correlation(vocal, [-y for y in center]),
            'corr_vocal_amplitude': correlation(vocal, amplitude),
            'corr_low_water_level_lift': correlation(bands[0], lifts),
            'corr_vocal_water_level_lift': correlation(vocal, lifts),
            'flat_fraction': flat_fraction,
            'crest_count': statistics.mean(crest_counts),
            'corr_impulse_amplitude': correlation(impulse, amplitude),
            'corr_impulse_center_rise': correlation(impulse, [-y for y in center]),
            'center_jump_p99': sorted(jumps)[math.ceil(len(jumps)*.99)-1] if jumps else 0.0,
            'travel_shift_median': statistics.median(shifts) if shifts else 0.0,
            'travel_explained_ratio': statistics.mean(explained) if explained else 0.0,
            'cohesion': cohesion,
            'spatial_correlation_length': statistics.mean(correlations) if correlations else 1.0,
            'corner_p99': sorted(curvature)[math.ceil(len(curvature) * .99) - 1] if curvature else 0.0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.log.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not rows:
        parser.error('log is empty')
    print(json.dumps(summarize(rows), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
