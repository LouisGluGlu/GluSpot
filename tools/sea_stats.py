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
    jumps = [abs(b - a) for a, b in zip(center, center[1:])]
    def stats(values):
        return {'min': min(values), 'max': max(values), 'std': statistics.pstdev(values)}
    return {'bands': [stats(band) for band in bands], 'center': stats(center),
            'amplitude_mean': statistics.mean(amplitude),
            'amplitude_p95': sorted(amplitude)[math.ceil(len(amplitude)*.95)-1],
            'corr_low_amplitude': correlation(bands[0], amplitude),
            'corr_low_center_rise': correlation(bands[0], [-y for y in center]),
            'corr_vocal_center_rise': correlation(vocal, [-y for y in center]),
            'corr_impulse_amplitude': correlation(impulse, amplitude),
            'corr_impulse_center_rise': correlation(impulse, [-y for y in center]),
            'center_jump_p99': sorted(jumps)[math.ceil(len(jumps)*.99)-1] if jumps else 0.0}


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
