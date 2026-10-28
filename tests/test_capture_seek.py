"""Deterministic mock-provider seek failures, timeouts and serialization."""
import asyncio
import unittest
from dataclasses import replace

from api.capture.contracts import TimelineSample
from api.capture.seek import SeekExecutor, SeekPolicy, SeekState


class Backend:
    def __init__(self, name, trace, *, moves=False, ack=True, fail=None):
        self.name, self.trace, self.moves, self.ack, self.fail = name, trace, moves, ack, fail
        self.sample = TimelineSample('app', 'song', 10, 100, 1000, is_playing=False, end_seconds=200)
        self.writes = 0

    async def read(self):
        self.trace.append(self.name + ':read')
        if self.fail == 'read-timeout':
            await asyncio.Future()
        return self.sample

    async def seek(self, target, *, request_id):
        self.writes += 1
        self.trace.append(self.name + ':write')
        if self.fail == 'write-timeout':
            await asyncio.Future()
        if self.fail == 'write-error':
            raise OSError('COM operation outcome unknown')
        if self.moves:
            self.sample = replace(self.sample, position_seconds=target)
        return self.ack


async def current():
    return True


class SeekExecutorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.trace = []
        self.executor = SeekExecutor(clock=lambda: 100, sleep=self.sleep)
        self.policy = SeekPolicy(read_delays=(0, 0, 0), request_timeout=.01, read_timeout=.01)
        self.smtc = Backend('smtc', self.trace)
        self.uia = Backend('uia', self.trace, moves=True)

    async def sleep(self, _delay):
        await asyncio.sleep(0)

    async def execute(self, **changes):
        kwargs = dict(identity=('app', 'song'), request_id='one', smtc=self.smtc,
                      fallback=self.uia, guard=current, policy=self.policy)
        kwargs.update(changes)
        return await self.executor.execute(80, **kwargs)

    async def test_verified_smtc_never_calls_uia(self):
        self.smtc.moves = True
        result = await self.execute()
        self.assertTrue(result.succeeded)
        self.assertEqual(self.uia.writes, 0)
        self.assertEqual(self.smtc.writes, 1)

    async def test_fake_ack_then_one_uia_fallback_after_all_smtc_reads(self):
        result = await self.execute()
        self.assertTrue(result.succeeded)
        self.assertEqual(result.backend, 'uia')
        self.assertEqual((self.smtc.writes, self.uia.writes), (1, 1))
        index = self.trace.index('uia:write')
        self.assertEqual(self.trace[:index].count('smtc:read'), 4)
        self.assertNotIn('smtc:write', self.trace[index:])
        self.assertEqual(await self.execute(), result)
        self.assertEqual((self.smtc.writes, self.uia.writes), (1, 1))

    async def test_fake_ack_is_failure_without_fallback(self):
        result = await self.execute(fallback=None)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.reason, 'fake-ack')

    async def test_uia_timeout_and_uncertain_smtc_never_double_seek(self):
        for failed_backend in ('smtc', 'uia'):
            with self.subTest(backend=failed_backend):
                self.setUp()
                backend = self.smtc if failed_backend == 'smtc' else self.uia
                backend.fail = 'write-timeout'
                result = await self.execute()
                self.assertEqual(result.state, SeekState.UNCERTAIN)
                self.assertEqual(self.smtc.writes, 1)
                self.assertEqual(self.uia.writes, 0 if failed_backend == 'smtc' else 1)
                await self.execute()
                self.assertEqual(self.smtc.writes, 1)

    async def test_uncertain_operation_blocks_even_new_request_ids(self):
        self.smtc.fail = 'write-timeout'
        await self.execute()
        second = await self.execute(request_id='two')
        self.assertEqual(second.reason, 'previous-request-uncertain')
        self.assertEqual(self.smtc.writes, 1)

    async def test_error_after_native_write_does_not_fallback(self):
        self.smtc.fail = 'write-error'
        result = await self.execute()
        self.assertEqual(result.state, SeekState.UNCERTAIN)
        self.assertEqual(self.uia.writes, 0)

    async def test_uia_read_timeout_fails_without_reissuing_mutation(self):
        self.uia.fail = 'read-timeout'
        result = await self.execute()
        self.assertFalse(result.succeeded)
        self.assertEqual(self.uia.writes, 1)

    async def test_concurrent_request_is_rejected(self):
        self.smtc.fail = 'write-timeout'
        first = asyncio.create_task(self.execute())
        await asyncio.sleep(0)
        second = await self.execute(request_id='two')
        self.assertEqual(second.reason, 'busy')
        await first
        self.assertEqual(self.smtc.writes, 1)

    async def test_identity_change_never_dispatches_fallback(self):
        calls = 0
        async def guard():
            nonlocal calls
            calls += 1
            return calls < 4
        result = await self.execute(guard=guard)
        self.assertEqual(result.state, SeekState.CANCELLED)
        self.assertEqual(self.uia.writes, 0)

    async def test_wrong_readback_identity_cancels(self):
        self.smtc.sample = replace(self.smtc.sample, track_id='new')
        result = await self.execute()
        self.assertEqual(result.state, SeekState.CANCELLED)
        self.assertEqual(self.smtc.writes, 0)

    async def test_optimistic_target_does_not_supply_verification(self):
        targets = []
        result = await self.execute(fallback=None, optimistic=targets.append)
        self.assertEqual(targets, [80])
        self.assertFalse(result.succeeded)
