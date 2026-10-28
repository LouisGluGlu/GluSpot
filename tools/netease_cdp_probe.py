"""Measure pushed NetEase progress and verified seeks on an existing CDP port.

This diagnostic never starts or stops NetEase. It changes playback position
three times, then attempts to restore the first observed position on the same
track. The final stdout line is always a machine-readable JSON summary.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path
import secrets
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.capture.cdp import CdpSession
from api.capture.cef import CEF_SEEK_POLICY, CefPositionSource, CefSeekBackend
from api.capture.seek import SeekExecutor


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _targets(sample) -> list[float]:
    """Keep diagnostic seeks clear of track end so restoring stays possible."""
    start = max(0.0, sample.start_seconds)
    end = sample.end_seconds
    if not math.isfinite(end) or end <= start:
        raise RuntimeError("A positive track duration is required for clamped seeks")
    upper = max(start, end - min(1.0, (end - start) / 10.0))
    return [max(start, min(upper, target)) for target in (15.0, 45.0, 90.0)]


async def run_probe(port: int, *, adapter="auto", timeout=10.0, sample_seconds=10.0,
                    emit=print) -> dict:
    """Attach, sample pushes, verify three seeks, and restore in a finally block.

    ``sample_seconds`` is injectable for tests; the command-line probe always
    samples for ten seconds. No position-polling task is created.
    """
    report = dict(passed=False, transport="existing-loopback-port", port=port,
                  sample_seconds=sample_seconds, samples=0, seeks=[], restore=None,
                  error=None)
    session = CdpSession(port, adapter=adapter)
    source = CefPositionSource(session)
    backend = CefSeekBackend(source)
    executor = SeekExecutor()
    original = None
    sampling = False
    arrival_times = []
    positions = []
    identities = set()
    mutated = False

    def receive(payload):
        if not sampling or payload is None:
            return
        arrival_times.append(time.monotonic())
        identities.add(str(payload.get("trackId", "")))
        position = payload.get("position")
        if isinstance(position, (int, float)) and math.isfinite(position):
            positions.append(float(position))

    unsubscribe = session.subscribe(receive)

    async def current():
        sample = await source.read()
        return original is not None and sample is not None and sample.identity == original.identity

    async def verified_seek(target, request_id):
        started = time.monotonic()
        result = await executor.execute(
            target, identity=original.identity, request_id=request_id,
            smtc=backend, fallback=None, guard=current,
            primary_name="cef", policy=CEF_SEEK_POLICY)
        sample = await source.read()
        return dict(target_seconds=target, succeeded=result.succeeded,
                    state=result.state.value, reason=result.reason,
                    readback_error_ms=(None if result.error_seconds is None
                                       else result.error_seconds * 1000.0),
                    readback_seconds=(sample.position_seconds if sample is not None else None),
                    elapsed_ms=(time.monotonic() - started) * 1000.0)

    try:
        await session.start()
        if not await session.wait_ready(timeout=timeout):
            raise RuntimeError("NetEase CDP attach/probe did not become ready")
        original = await source.wait_sample(timeout=timeout)
        if original is None:
            raise RuntimeError("NetEase did not push a physical progress sample")
        targets = _targets(original)
        report["original_position_seconds"] = original.position_seconds
        report["duration_seconds"] = original.end_seconds
        emit(f"Sampling pushed progress for {sample_seconds:g}s...")
        sampling = True
        await asyncio.sleep(sample_seconds)
        sampling = False
        report["samples"] = len(arrival_times)
        report["sample_rate_hz"] = len(arrival_times) / sample_seconds if sample_seconds > 0 else 0.0
        report["sample_span_seconds"] = (arrival_times[-1] - arrival_times[0]
                                          if len(arrival_times) > 1 else 0.0)
        report["position_range_seconds"] = [min(positions), max(positions)] if positions else None
        report["tracks_observed"] = len(identities)
        if not arrival_times:
            raise RuntimeError("No pushed progress arrived during the sampling window")
        if not await current():
            raise RuntimeError("Track changed while sampling; refusing to seek a different track")
        for number, target in enumerate(targets, 1):
            mutated = True
            result = await verified_seek(target, f"probe-{number}-{secrets.token_hex(8)}")
            report["seeks"].append(result)
            error = result["readback_error_ms"]
            error_text = "unavailable" if error is None else f"{error:+.1f} ms"
            emit(f"Seek {number}: {target:.3f}s, readback error {error_text}, {result['reason']}")
            if result["state"] in {"uncertain", "cancelled"}:
                break
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        emit(report["error"])
    finally:
        sampling = False
        if mutated and original is not None:
            try:
                restored = await verified_seek(original.position_seconds, f"probe-restore-{secrets.token_hex(8)}")
                report["restore"] = restored
                error = restored["readback_error_ms"]
                error_text = "unavailable" if error is None else f"{error:+.1f} ms"
                emit(f"Restore: {original.position_seconds:.3f}s, readback error {error_text}, {restored['reason']}")
            except Exception as error:
                report["restore"] = dict(succeeded=False, reason=f"{type(error).__name__}: {error}")
        unsubscribe()
        source.close()
        await session.close()
    report["passed"] = (report["error"] is None and len(report["seeks"]) == 3
                        and all(item["succeeded"] for item in report["seeks"])
                        and bool(report["restore"] and report["restore"]["succeeded"]))
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=_port, required=True,
                        help="Existing cloudmusic.exe remote-debugging port on 127.0.0.1")
    parser.add_argument("--adapter", default="auto", help="Versioned JS adapter (default: auto)")
    parser.add_argument("--timeout", type=float, default=10.0,
                        help="Attach and first-sample deadline in seconds (default: 10)")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be positive and finite")
    try:
        report = asyncio.run(run_probe(args.port, adapter=args.adapter, timeout=args.timeout))
    except KeyboardInterrupt:
        report = dict(passed=False, error="interrupted")
    except Exception as error:
        report = dict(passed=False, error=f"{type(error).__name__}: {error}")
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
