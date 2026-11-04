"""Explicit NetEase Pause learns a page-issued contract, without preparing CDP."""
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock

from api.capture.cdp import CdpSession
from api.capture.netease_js import JsAdapterRegistry
from tests.capture_async import CaptureAsyncTestCase


class CdpPauseTests(CaptureAsyncTestCase):
    def session(self):
        session = CdpSession(12345, clock=lambda: 100)
        session._ready.set()
        session.generation = 7
        session._sample(dict(trackId='native-song', position=12, duration=100,
                             playing=True, sequence=1))
        session._evaluate = AsyncMock()
        return session

    async def test_pause_does_not_require_a_previously_observed_manual_pause(self):
        session = self.session()
        session.start = AsyncMock(side_effect=AssertionError('must not prepare CDP'))
        session._evaluate.side_effect = [dict(available=True, trackId='native-song'), True,
            dict(trackId='native-song', position=12, duration=100, playing=False, sequence=2)]
        self.assertTrue(await session.pause('native-song', 'direct'))
        expressions = [call.args[0] for call in session._evaluate.await_args_list]
        self.assertTrue(all('observePause' not in value for value in expressions))
        self.assertIn('.pause?.(', expressions[1])
        self.assertEqual(session.pause_diagnostic, 'paused')
        session.start.assert_not_called()

    async def test_missing_pause_contract_does_not_send_pause(self):
        session = self.session()
        session._pause_observation_generation = 7
        session._evaluate.return_value = dict(available=False, trackId='native-song')
        self.assertFalse(await session.pause('native-song', 'request'))
        self.assertEqual(session.pause_diagnostic, 'contract-unavailable')
        self.assertEqual(session._evaluate.await_count, 1)
        self.assertIn('pauseCapability', session._evaluate.await_args.args[0])
        self.assertNotIn('.pause?.(', session._evaluate.await_args.args[0])

    async def test_not_ready_pause_does_zero_writes_or_launches(self):
        session = self.session()
        session.start = AsyncMock(side_effect=AssertionError('must not prepare CDP'))
        session._ready.clear()
        self.assertFalse(await session.pause('native-song', 'one'))
        self.assertEqual(session.pause_diagnostic, 'not-ready')
        self.assertFalse(await session.set_pause_observation_enabled(True))
        session._evaluate.assert_not_called()
        session.start.assert_not_called()

    async def test_old_adapter_missing_capability_remains_unavailable(self):
        session = self.session()
        session._evaluate.return_value = None
        self.assertFalse(await session.set_pause_observation_enabled(True))
        self.assertFalse(await session.pause('native-song', 'one'))
        self.assertEqual(session.pause_diagnostic, 'contract-unavailable')
        self.assertEqual(session._evaluate.await_count, 2)

    async def test_observed_contract_pause_has_independent_readback_and_no_reload(self):
        session = self.session()
        session._evaluate.side_effect = [True, dict(available=True, trackId='native-song'), True,
                                       dict(trackId='native-song', position=12, duration=100,
                                            playing=False, sequence=1)]
        self.assertTrue(await session.set_pause_observation_enabled(True))
        self.assertTrue(await session.pause('native-song', 'request', expected_generation=7))
        expressions = [call.args[0] for call in session._evaluate.await_args_list]
        self.assertIn('observePause', expressions[0])
        self.assertIn('.pause?.(', expressions[2])
        self.assertIn('.read()', expressions[3])
        self.assertFalse(any('reload' in value or '.start(' in value or '.seek(' in value
                             or 'toggle' in value for value in expressions))
        self.assertIs(session.latest['playing'], False)
        self.assertFalse(await session.pause('native-song', 'request', expected_generation=7))
        self.assertEqual(session._evaluate.await_count, 4)

    async def test_new_intent_or_wrong_track_generation_prevents_dispatch(self):
        session = self.session()
        session._pause_observation_generation = 7
        for track, generation in [('different', 7), ('native-song', 6)]:
            self.assertFalse(await session.pause(track, 'request', expected_generation=generation))
        session._evaluate.assert_not_called()
        guards = iter([True, False])
        session._evaluate.return_value = dict(available=True, trackId='native-song')
        self.assertFalse(await session.pause('native-song', 'request', guard=lambda: next(guards)))
        self.assertEqual(session._evaluate.await_count, 1)

    async def test_accepted_without_raw_pause_is_not_success_and_not_retried(self):
        session = self.session()
        session._pause_observation_generation = 7
        session._evaluate.side_effect = [dict(available=True, trackId='native-song'), True,
                                       dict(trackId='native-song', position=12, duration=100,
                                            playing=True, sequence=1)]
        self.assertFalse(await session.pause('native-song', 'request'))
        self.assertFalse(await session.pause('native-song', 'request'))
        self.assertEqual(session._evaluate.await_count, 3)
        self.assertIs(session.latest['playing'], True)

    async def test_disable_restores_observation_and_invalidated_enable_cleans_it_up(self):
        session = self.session()
        session._evaluate.return_value = True
        self.assertTrue(await session.set_pause_observation_enabled(True))
        self.assertTrue(await session.set_pause_observation_enabled(True))
        self.assertEqual(session._evaluate.await_count, 1)
        self.assertTrue(await session.set_pause_observation_enabled(False))
        self.assertIsNone(session._pause_observation_generation)
        guards = iter([True, False])
        self.assertFalse(await session.set_pause_observation_enabled(True, guard=lambda: next(guards)))
        self.assertIsNone(session._pause_observation_generation)
        self.assertEqual(session._evaluate.await_count, 4)

    async def test_disable_cleans_observation_when_enable_ack_was_cancelled(self):
        session = self.session()
        entered, expressions = asyncio.Event(), []
        async def evaluate(expression):
            expressions.append(expression)
            if 'observePause?.(true)' in expression:
                entered.set()
                await asyncio.Future()  # Native dispatch happened; ACK never arrived.
            return True
        session._evaluate = evaluate
        task = asyncio.create_task(session.set_pause_observation_enabled(True))
        await entered.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(await session.set_pause_observation_enabled(False))
        self.assertIsNone(session._pause_observation_requested_generation)
        self.assertEqual(len(expressions), 2)
        self.assertIn('observePause?.(false)', expressions[-1])

    async def test_uncertain_disable_retains_cleanup_authority_for_later_close(self):
        session = self.session()
        session._pause_observation_requested_generation = 7
        session._evaluate.side_effect = [asyncio.TimeoutError(), True]
        with self.assertRaises(asyncio.TimeoutError):
            await session.set_pause_observation_enabled(False)
        self.assertEqual(session._pause_observation_requested_generation, 7)
        self.assertTrue(await session.set_pause_observation_enabled(False))
        self.assertIsNone(session._pause_observation_requested_generation)
        self.assertEqual(session._evaluate.await_count, 2)


@unittest.skipUnless(shutil.which('node'), 'existing Node runtime unavailable')
class PauseJavascriptTests(unittest.TestCase):
    def run_js(self, harness):
        fixture = (Path(__file__).parent / 'fixtures/capture/netease_native_callbacks.js').read_text()
        setup = "const fake = makeNativeFixture(); const {legacyNativeCmder, registered} = fake;\n"
        script = fixture + '\n' + setup + harness.replace('SCRIPT', json.dumps(
            JsAdapterRegistry.select().install_expression()))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pause.js'
            path.write_text(script)
            return json.loads(subprocess.run([shutil.which('node'), str(path)], check=True,
                                             capture_output=True, text=True).stdout)

    def test_pause_uses_seek_channel_and_stays_valid_after_track_change(self):
        result = self.run_js(r'''
globalThis.performance = {now:()=>100};
globalThis.setInterval = ()=>1; globalThis.clearInterval = ()=>{};
globalThis.__gluspotNeteaseSample = ()=>{};
const calls = [], token = {}, channel = {call(method, callback, args) {
  calls.push({method,args,receiver:this===channel});
  if (method === 'audioplayer.pause') registered.PlayState(false);
  callback(); return token;
}};
const original = channel.call;
eval(SCRIPT); const adapter = globalThis.__gluspotNeteaseV1;
registered.PlayState(true); registered.PlayProgress('native-song',12,.12);
const ready = adapter.pauseCapability().available;
const paused = adapter.pause('native-song','one');
const rawPaused = adapter.read().playing === false;
const duplicate = adapter.pause('native-song','one');
registered.PlayState(true); registered.PlayProgress('replacement',1,.01);
const replacement = adapter.pause('replacement','two');
const restored = channel.call === original;
adapter.close();
console.log(JSON.stringify({ready,paused,rawPaused,duplicate,replacement,restored,calls}));
''')
        self.assertTrue(result['ready'] and result['paused'] and result['rawPaused'] and result['replacement'])
        self.assertFalse(result['duplicate'])
        self.assertTrue(result['restored'])
        self.assertEqual(result['calls'], [
            dict(method='audioplayer.pause', args=['native-song'], receiver=True),
            dict(method='audioplayer.pause', args=['replacement'], receiver=True),
        ])

    def test_pause_is_refused_until_the_current_track_is_playing(self):
        result = self.run_js(r'''
globalThis.performance = {now:()=>100};
globalThis.setInterval = ()=>1; globalThis.clearInterval = ()=>{};
globalThis.__gluspotNeteaseSample = ()=>{};
const calls = [];
const channel = {call(method, callback, args) {
  calls.push(method);
  if (method === 'audioplayer.pause') registered.PlayState(false);
  callback();
}};
eval(SCRIPT); const adapter = globalThis.__gluspotNeteaseV1;
registered.PlayProgress('native-song',12,.12);
const beforeState = adapter.pause('native-song','early');
registered.PlayState(true);
const sent = adapter.pause('native-song','one');
adapter.close();
console.log(JSON.stringify({beforeState,sent,calls}));
''')
        self.assertFalse(result['beforeState'])
        self.assertTrue(result['sent'])
        self.assertEqual(result['calls'], ['audioplayer.pause'])
