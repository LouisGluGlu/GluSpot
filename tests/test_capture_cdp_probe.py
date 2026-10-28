"""Diagnostic lifecycle uses real CEF readback and never controls processes."""
import asyncio
from contextlib import redirect_stdout
import io
import json
import time
import unittest
from unittest.mock import patch

from tools import netease_cdp_probe
from tests.capture_cdp_fake import FakeCdpServer


class PushedSession:
    def __init__(self, port, *, adapter="auto", move=True, fail=False, replace=False):
        self.port, self.move, self.fail, self.replace = port, move, fail, replace
        self.ready = False
        self.latest = None
        self.callbacks = []
        self.writes = []
        self.position = 12.0
        self.task = None
        self.sequence = 0
        self.closed = False

    def subscribe(self, callback):
        self.callbacks.append(callback)
        return lambda: self.callbacks.remove(callback)

    async def start(self):
        self.ready = True
        self.push()
        self.task = asyncio.create_task(self.progress())

    async def wait_ready(self, timeout=5):
        return self.ready

    def push(self):
        self.sequence += 1
        self.latest = dict(trackId="replacement" if self.replace and self.sequence > 3 else "song",
                           position=self.position, duration=50.0, playing=False, rate=1.0,
                           received_at=time.monotonic(), sequence=self.sequence, generation=1)
        for callback in tuple(self.callbacks):
            callback(self.latest)

    async def progress(self):
        while True:
            await asyncio.sleep(.005)
            self.push()

    async def readback(self):
        return self.latest

    async def seek(self, track_id, request_id, seconds):
        self.writes.append((track_id, request_id, seconds))
        if self.fail:
            raise OSError("mutation outcome unknown")
        if self.move:
            self.position = seconds
            self.push()
        return True

    async def close(self):
        self.closed = True
        self.ready = False
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def run_probe(self, **options):
        session = PushedSession(12345, **options)
        messages = []
        with patch.object(netease_cdp_probe, "CdpSession", return_value=session):
            result = await netease_cdp_probe.run_probe(12345, sample_seconds=.03,
                                                      timeout=.2, emit=messages.append)
        self.assertTrue(session.closed)
        self.assertEqual(session.callbacks, [])
        return result, session, messages

    async def test_three_clamped_seeks_and_original_position_restored(self):
        result, session, messages = await self.run_probe()
        self.assertTrue(result["passed"], result)
        self.assertGreater(result["samples"], 0)
        self.assertEqual([item[2] for item in session.writes], [15, 45, 49, 12])
        self.assertEqual(len({item[1] for item in session.writes}), 4)
        self.assertTrue(all(item["readback_error_ms"] == 0 for item in result["seeks"]))
        self.assertEqual(session.position, 12)
        self.assertEqual(sum("readback error" in message for message in messages), 4)

    async def test_track_change_during_sampling_refuses_all_mutations(self):
        session = PushedSession(12345, replace=True)

        async def deliver_samples(_seconds):
            # start() pushed the original; deliver both identities while sampling.
            session.push()
            session.push()
            session.push()

        with patch.object(netease_cdp_probe, "CdpSession", return_value=session), \
             patch.object(netease_cdp_probe.asyncio, "sleep", side_effect=deliver_samples):
            result = await netease_cdp_probe.run_probe(12345, sample_seconds=.03,
                                                      timeout=.2, emit=lambda _: None)
        self.assertTrue(session.closed)
        self.assertEqual(session.callbacks, [])
        self.assertEqual(result["tracks_observed"], 2)
        self.assertFalse(result["passed"])
        self.assertIn("Track changed", result["error"])
        self.assertEqual(session.writes, [])
        self.assertIsNone(result["restore"])

    async def test_acknowledged_seek_without_physical_change_fails(self):
        result, session, _ = await self.run_probe(move=False)
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["seeks"]), 3)
        self.assertTrue(all(not item["succeeded"] for item in result["seeks"]))
        self.assertTrue(all(item["readback_error_ms"] != 0 for item in result["seeks"]))
        self.assertEqual(session.writes[-1][2], 12)

    async def test_uncertain_mutation_prevents_later_seeks_and_restore(self):
        result, session, _ = await self.run_probe(fail=True)
        self.assertFalse(result["passed"])
        self.assertEqual(len(session.writes), 1)
        self.assertEqual(result["seeks"][0]["state"], "uncertain")
        self.assertEqual(result["restore"]["reason"], "previous-request-uncertain")

    async def test_stdlib_fake_cdp_server_round_trip_and_restore(self):
        async with FakeCdpServer() as server:
            server.sample = dict(server.sample, playing=False)

            async def push_progress():
                while True:
                    await asyncio.sleep(.005)
                    server.sample = dict(server.sample, sequence=server.sample['sequence'] + 1)
                    await server.emit(server.sample)

            task = asyncio.create_task(push_progress())
            try:
                result = await netease_cdp_probe.run_probe(server.port, sample_seconds=.04,
                                                          timeout=1, emit=lambda _: None)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.assertTrue(result['passed'], result)
            self.assertEqual(server.sample['position'], 12.0)
            seeks = [message for message in server.commands
                     if message['method'] == 'Runtime.evaluate'
                     and '.seek(' in message['params'].get('expression', '')]
            self.assertEqual(len(seeks), 4)
            self.assertGreater(result['samples'], 0)


class ProbeCliTests(unittest.TestCase):
    def test_last_stdout_line_is_json_and_exit_follows_result(self):
        async def failed_probe(*args, **kwargs):
            print("diagnostic detail")
            return {"passed": False, "error": "sample error"}
        output = io.StringIO()
        with patch.object(netease_cdp_probe, "run_probe", side_effect=failed_probe), redirect_stdout(output):
            self.assertEqual(netease_cdp_probe.main(["--port", "12345"]), 1)
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1]),
                         {"passed": False, "error": "sample error"})

    def test_port_rejects_nonlocal_invalid_range(self):
        for value in ("0", "65536", "-1", "localhost:9222"):
            with self.subTest(value=value), self.assertRaises(Exception):
                netease_cdp_probe._port(value)
