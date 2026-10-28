import asyncio
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api.capture.cdp import CdpError, CdpSession, discover_target
from api.capture.netease import NetEaseInstallation, NetEaseLocator
from api.capture.netease_js import JsAdapterRegistry
from tests.capture_cdp_fake import FakeCdpServer, close_writer

from tests.capture_async import CaptureAsyncTestCase


class CdpTests(CaptureAsyncTestCase):
    async def asyncSetUp(self):
        self.server = await FakeCdpServer().__aenter__()
        self.events = []
        self.session = CdpSession(self.server.port, on_sample=self.events.append,
                                  command_timeout=0.3, reconnect_delays=(0.01,))

    async def asyncTearDown(self):
        await self.session.close()
        await self.server.close()

    async def ready(self):
        await self.session.start()
        self.assertTrue(await self.session.wait_ready(timeout=1))
        for _ in range(20):
            if self.session.latest:
                return
            await asyncio.sleep(0.005)
        self.fail('missing binding sample')

    async def test_discovers_exact_target_among_multiple_pages(self):
        target = await discover_target(self.server.port)
        self.assertEqual(target['id'], 'player')

    async def test_rejects_foreign_websocket_host(self):
        self.server.target_host = 'example.com'
        with self.assertRaises(CdpError):
            await discover_target(self.server.port)

    async def test_rejects_non_player_target(self):
        self.server.target_url = 'https://example.org/'
        with self.assertRaises(CdpError):
            await discover_target(self.server.port)

    async def test_attach_binding_event_and_readback_are_raw(self):
        await self.ready()
        self.assertTrue(self.session.probe['api'])
        self.assertEqual(self.session.probe['hook'], 'load-time-native-chain-v1')
        sample = self.session.latest
        self.assertEqual(sample['position'], 12)
        readback = await self.session.readback()
        self.assertEqual(sample, readback)
        self.assertEqual(self.session.samples, 1)
        self.assertEqual([m['method'] for m in self.server.commands][:3],
                         ['Page.enable', 'Page.addScriptToEvaluateOnNewDocument',
                          'Runtime.enable'])
        methods = [m['method'] for m in self.server.commands]
        self.assertEqual(self.server.reloads, 1)
        self.assertLess(methods.index('Page.addScriptToEvaluateOnNewDocument'),
                        methods.index('Runtime.enable'))
        self.assertLess(methods.index('Runtime.enable'), methods.index('Page.reload'))
        script_command = next(item for item in self.server.commands
                             if item['method'] == 'Page.addScriptToEvaluateOnNewDocument')
        self.assertIn('__gluspotNeteaseEarlyV1', script_command['params']['source'])

    async def test_seek_uses_native_binding_for_new_evidence(self):
        await self.ready()
        before = self.session.latest
        self.assertTrue(await self.session.seek('native-1', 'request " id', 44.0))
        for _ in range(20):
            if self.session.latest['sequence'] > before['sequence']:
                break
            await asyncio.sleep(0.005)
        self.assertEqual(self.session.latest['position'], 44)
        self.assertGreaterEqual(self.session.latest['received_at'], before['received_at'])

    async def test_parallel_commands_have_distinct_responses(self):
        await self.ready()
        results = await asyncio.gather(*(self.session.command('Runtime.enable') for _ in range(30)))
        self.assertEqual(results, [{}] * 30)
        ids = [m['id'] for m in self.server.commands]
        self.assertEqual(len(ids), len(set(ids)))

    async def test_bad_samples_do_not_replace_valid_observation(self):
        await self.ready()
        sample = self.session.latest
        for payload in ({}, {'type':'sample','trackId':'x','position':math.nan,'sequence':2},
                        dict(self.server.sample, position=-1), dict(self.server.sample, duration='bad')):
            await self.server.emit(payload)
        await asyncio.sleep(0.02)
        self.assertEqual(self.session.latest, sample)

    async def test_state_without_progress_keeps_original_position_time(self):
        await self.ready()
        before = self.session.latest
        await self.server.emit(dict(self.server.sample, playing=False, event='PlayState'))
        await asyncio.sleep(0.02)
        self.assertFalse(self.session.latest['playing'])
        self.assertEqual(self.session.latest['received_at'], before['received_at'])
        self.assertEqual(self.session.latest['sequence'], before['sequence'])
        self.assertGreaterEqual(self.session.latest['state_received_at'], before['received_at'])
        state_received_at = self.session.latest['state_received_at']
        self.server.sample = dict(self.server.sample, playing=False, event='PlayState')
        await self.server.emit(self.server.sample)
        await asyncio.sleep(0.01)
        self.assertEqual(self.session.latest['state_received_at'], state_received_at)
        self.assertEqual((await self.session.readback())['state_received_at'], state_received_at)

    async def test_reconnect_invalidates_and_reinstalls_adapter(self):
        await self.ready()
        generation = self.session.generation
        await self.server.drop_connections()
        for _ in range(100):
            if self.session.generation > generation and self.session.ready:
                break
            await asyncio.sleep(0.01)
        self.assertGreater(self.session.generation, generation)
        self.assertIn(None, self.events)
        self.assertTrue(self.session.ready)
        self.assertGreaterEqual(self.server.connections, 2)
        self.assertEqual(self.server.reloads, 1)

    async def test_probe_failure_never_becomes_ready(self):
        self.server.reject_probe = True
        await self.session.start()
        self.assertFalse(await self.session.wait_ready(timeout=0.25))
        self.assertFalse(await self.session.seek('native-1', 'no', 3))
        self.assertEqual(self.session.probe, dict(api=False, reason='native-callback-chain-unavailable'))
        self.assertEqual(self.session.diagnostics()['reason'], 'native-callback-chain-unavailable')

    async def test_close_restores_adapter_before_removing_binding_and_socket(self):
        await self.ready()
        await self.session.close()
        cleanup = self.server.commands[-3:]
        self.assertEqual(cleanup[0]['method'], 'Runtime.evaluate')
        self.assertEqual(cleanup[0]['params']['expression'], self.session.adapter.close_expression())
        self.assertEqual(cleanup[1]['method'], 'Runtime.removeBinding')
        self.assertEqual(cleanup[2]['method'], 'Page.removeScriptToEvaluateOnNewDocument')

    async def test_close_during_probe_failure_cleanup_finishes_restoration(self):
        self.server.reject_probe = True
        entered = asyncio.Event()
        evaluate = self.session._evaluate
        blocked = False

        async def block_first_cleanup(expression):
            nonlocal blocked
            if expression == self.session.adapter.close_expression() and not blocked:
                blocked = True
                entered.set()
                await asyncio.Future()
            return await evaluate(expression)

        self.session._evaluate = block_first_cleanup
        await self.session.start()
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(self.session.close(), 1)
        self.assertIsNone(self.session._ws)
        self.assertIsNone(self.session._reader_task)
        self.assertEqual(self.session._pending, {})
        self.assertEqual(self.server.commands[-1]['method'], 'Page.removeScriptToEvaluateOnNewDocument')

    async def test_chain_loss_invalidates_evidence_and_records_health_reason(self):
        await self.ready()
        self.server.reject_probe = True
        await self.server.emit(dict(type='unavailable', reason='native-callback-chain-changed'))
        for _ in range(30):
            if not self.session.ready:
                break
            await asyncio.sleep(.005)
        self.assertFalse(self.session.ready)
        self.assertIsNone(self.session.latest)
        self.assertIn(None, self.events)
        self.assertIn(self.session.diagnostics()['reason'],
                      {'native-callback-chain-changed', 'native-callback-chain-unavailable'})

    async def test_context_replacement_reconnects(self):
        await self.ready()
        generation = self.session.generation
        for writer in tuple(self.server.clients):
            await self.server._send(writer, {'method':'Runtime.executionContextsCleared'})
        for _ in range(100):
            if self.session.generation > generation and self.session.ready:
                break
            await asyncio.sleep(0.01)
        self.assertGreater(self.session.generation, generation)

    async def test_three_missing_heartbeats_invalidate(self):
        await self.ready()
        self.server.send_heartbeats = False
        generation = self.session.generation
        self.session._last_heartbeat -= 4
        for _ in range(150):
            if self.session.generation > generation:
                break
            await asyncio.sleep(0.01)
        self.assertGreater(self.session.generation, generation)
        self.assertIn(None, self.events)

    async def test_unsubscribe_and_close_release_callbacks(self):
        called = []
        unsubscribe = self.session.subscribe(called.append)
        unsubscribe()
        await self.ready()
        await self.session.close()
        self.assertEqual(called, [])
        self.assertFalse(self.session.ready)
        self.assertIsNone(self.session.latest)


class LocatorTests(CaptureAsyncTestCase):
    def locator(self, **kwargs):
        return NetEaseLocator(processes=lambda: [], registry_paths=lambda: [],
                              exists=lambda path: True, version=lambda path: '3.1.42', **kwargs)

    async def test_launch_argv_has_random_loopback_port(self):
        calls = []
        locator = self.locator(path='/apps/cloudmusic.exe', spawn=lambda args, **kw: calls.append((args, kw)) or SimpleNamespace(pid=123))
        launch = await locator.launch()
        self.assertTrue(1 <= launch.port <= 65535)
        self.assertEqual(calls[0][0], ['/apps/cloudmusic.exe', f'--remote-debugging-port={launch.port}',
                                     '--remote-debugging-address=127.0.0.1'])
        self.assertEqual(launch.pid, 123)

    async def test_running_player_requires_explicit_relaunch(self):
        locator = NetEaseLocator('/apps/cloudmusic.exe', processes=lambda: [(1, '/apps/cloudmusic.exe')],
                                 registry_paths=lambda: [], exists=lambda _: True, version=lambda _: '')
        with self.assertRaisesRegex(RuntimeError, 'explicit relaunch'):
            await locator.launch()

    async def test_relaunch_stops_only_exact_path(self):
        running = [(1, '/apps/cloudmusic.exe'), (2, '/other/cloudmusic.exe')]
        stopped = []
        def stop(pid):
            stopped.append(pid)
            running[:] = [(p, path) for p, path in running if p != pid]
        locator = NetEaseLocator('/apps/cloudmusic.exe', processes=lambda: list(running),
                                 registry_paths=lambda: [], exists=lambda _: True, version=lambda _: '',
                                 stop=stop, spawn=lambda *a, **k: SimpleNamespace(pid=3))
        launch = await locator.launch(relaunch=True)
        self.assertEqual(stopped, [1])
        self.assertEqual(running, [(2, '/other/cloudmusic.exe')])
        self.assertEqual(launch.pid, 3)

    async def test_locator_uses_process_path_and_version(self):
        locator = NetEaseLocator(processes=lambda: [(7, r'D:\Apps\CloudMusic\cloudmusic.exe')],
                                 registry_paths=lambda: [], exists=lambda _: True, version=lambda _: '3.1.42')
        found = locator.locate()
        self.assertEqual(found.pids, (7,))
        self.assertEqual(found.version, '3.1.42')

    async def test_rejects_other_executable(self):
        locator = self.locator(path='/apps/other.exe')
        with patch.dict(os.environ, {'ProgramFiles': '', 'ProgramFiles(x86)': '', 'LOCALAPPDATA': ''}):
            self.assertIsNone(locator.locate())
        with self.assertRaises(ValueError):
            await locator.launch(NetEaseInstallation('/apps/other.exe'))

    async def test_unknown_version_still_requires_feature_probe(self):
        self.assertEqual(JsAdapterRegistry.select('99.1').name, 'legacy-native-v1')
        with self.assertRaises(ValueError):
            JsAdapterRegistry.select('3.1.42', 'invented')


class AdapterJavascriptTests(unittest.TestCase):
    def run_javascript(self, harness, *, existing=True):
        fixture = (Path(__file__).parent / 'fixtures/capture/netease_native_callbacks.js').read_text()
        setup = ('const fake = makeNativeFixture({existing:' + str(existing).lower() + '});'
                 'const {legacyNativeCmder, registered} = fake;\n')
        script = fixture + '\n' + setup + harness.replace(
            'SCRIPT', json.dumps(JsAdapterRegistry.select().install_expression()))
        return json.loads(subprocess.run([shutil.which('node'), '-e', script],
                                         check=True, text=True, capture_output=True).stdout)

    @unittest.skipUnless(shutil.which('node'), 'optional existing Node runtime unavailable')
    def test_document_start_shim_wraps_late_registration_and_detach_keeps_original(self):
        early = json.dumps(JsAdapterRegistry.select().early_install_expression())
        value = self.run_javascript(r'''globalThis.legacyNativeCmder = legacyNativeCmder;
const early = eval(EARLY);
const observed = [], neteaseCalls = [], slots = {}, trace = [], results = [];
for (const event of fake.events) {
  const key = 'audioplayer.on' + event;
  legacyNativeCmder.registerCallAdapter(key, function(...args) {
    neteaseCalls.push({event, receiver:this, args}); trace.push('native-' + event);
    return 'native-' + event;
  }, 'audioplayer');
  slots[event] = fake.nativeSlots[key];
}
globalThis.__gluspotNeteaseSample = value => { trace.push('observer'); observed.push(JSON.parse(value)); };
globalThis.performance = {now: () => 100};
globalThis.setInterval = () => 1; globalThis.clearInterval = () => {};
const probe = eval(SCRIPT);
for (const event of fake.events) results.push(fake.registered[event].call({event}, 'song', 5, .1));
const beforeClose = {calls:neteaseCalls.length, observed:observed.length,
  health:globalThis.__gluspotNeteaseV1.health()};
globalThis.__gluspotNeteaseV1.close();
for (const event of fake.events) fake.registered[event]('song', 6, .2);
console.log(JSON.stringify({probe, beforeClose, afterClose:{calls:neteaseCalls.length, observed:observed.length},
  slotsStillOwned:fake.events.every(event => fake.nativeSlots['audioplayer.on'+event] === slots[event]), results, trace}));
'''.replace('EARLY', early), existing=False)
        self.assertTrue(value['probe']['api'])
        self.assertEqual(value['beforeClose']['calls'], 4)
        self.assertEqual(value['results'], ['native-' + event for event in ('PlayProgress', 'PlayState', 'Load', 'End')])
        self.assertEqual(value['beforeClose']['health']['discovery']['hook'], 'load-time-native-chain-v1')
        self.assertEqual(value['beforeClose']['health']['originalCalls'],
                         dict(PlayProgress=1, PlayState=1, Load=1, End=1))
        self.assertGreater(value['beforeClose']['observed'], 0)
        self.assertEqual(value['trace'][:2], ['native-PlayProgress', 'observer'])
        self.assertEqual(value['afterClose']['calls'], 8)
        self.assertEqual(value['afterClose']['observed'], value['beforeClose']['observed'])
        self.assertTrue(value['slotsStillOwned'])

    @unittest.skipUnless(shutil.which('node'), 'optional existing Node runtime unavailable')
    def test_single_slot_model_reproduces_first_append_displacement(self):
        value = self.run_javascript(r'''
registered.PlayProgress('song', 1, .1);
let observed = 0;
legacyNativeCmder.appendRegisterCall('PlayProgress', 'audioplayer', () => observed++);
const stub = fake.nativeSlots['audioplayer.onPlayProgress'];
legacyNativeCmder.appendRegisterCall('PlayProgress', 'audioplayer', () => observed++);
registered.PlayProgress('song', 2, .2);
console.log(JSON.stringify({originalCalls:fake.calls.length, observed,
  registrations:fake.registrations.length, sameStub:stub === fake.nativeSlots['audioplayer.onPlayProgress']}));
''', existing=False)
        self.assertEqual(value, dict(originalCalls=1, observed=2, registrations=1, sameStub=True))

    @unittest.skipUnless(shutil.which('node'), 'optional existing Node runtime unavailable')
    def test_document_start_shim_miss_keeps_clean_slot_refusal(self):
        early = json.dumps(JsAdapterRegistry.select().early_install_expression())
        value = self.run_javascript(r'''globalThis.legacyNativeCmder = legacyNativeCmder;
eval(EARLY);
const probe = eval(SCRIPT);
console.log(JSON.stringify({probe, registrations:fake.registrations.length,
  keys:Object.keys(legacyNativeCmder.registerCallMap), installed:!!globalThis.__gluspotNeteaseV1}));
'''.replace('EARLY', early), existing=False)
        self.assertEqual(value['probe'], dict(api=False, reason='native-callback-chain-unavailable'))
        self.assertEqual(value['registrations'], 0)
        self.assertEqual(value['keys'], [])
        self.assertFalse(value['installed'])

    @unittest.skipUnless(shutil.which('node'), 'optional existing Node runtime unavailable')
    def test_native_callbacks_chain_first_and_restore_exactly_with_idempotent_install(self):
        value = self.run_javascript(r'''
const before = fake.events.map(event => {
  const key = 'audioplayer.on' + event;
  const queue = legacyNativeCmder.registerCallMap[key];
  // Retain an earlier player callback too; both must run before GluSpot.
  queue.unshift(function() { fake.calls.push({event:'earlier'}); });
  Object.defineProperty(queue, '1', {value:fake.originals[event],
    writable:true, enumerable:false, configurable:false});
  return {queue, descriptor:Object.getOwnPropertyDescriptor(queue, '1'),
          slot:fake.nativeSlots[key], stub:legacyNativeCmder.registerCallStubs[key]};
});
const pushed = [], ordering = [];
let now = 0, timers = 0;
globalThis.performance = {now: () => now};
globalThis.setInterval = () => ++timers; globalThis.clearInterval = () => {};
globalThis.__gluspotNeteaseSample = value => {
  pushed.push(JSON.parse(value)); ordering.push(fake.calls.map(call => call.event));
};
const first = eval(SCRIPT), adapter = globalThis.__gluspotNeteaseV1;
const wrappers = before.map(item => item.queue[1]);
const second = eval(SCRIPT);
const sameWrappers = before.every((item, index) => item.queue[1] === wrappers[index]);
const receiver = {};
const result = registered.PlayProgress.call(receiver, 'song', 5, .1);
registered.PlayState(true); registered.End(); registered.Load();
const health = adapter.health();
const sameReceiver = fake.calls[1].receiver === receiver;
const sameArguments = JSON.stringify(fake.calls[1].args) === JSON.stringify(['song',5,.1]);
adapter.close(); adapter.close();
const restored = before.every((item,index) => {
  const key = 'audioplayer.on' + fake.events[index];
  const actual = Object.getOwnPropertyDescriptor(item.queue, '1');
  return Object.keys(item.descriptor).every(key => actual[key] === item.descriptor[key]) &&
    fake.nativeSlots[key] === item.slot && legacyNativeCmder.registerCallStubs[key] === item.stub;
});
const count = pushed.length;
registered.PlayProgress('song', 6, .12);
const dormant = pushed.length === count;
const third = eval(SCRIPT);
registered.PlayProgress('song', 7, .14);
adapter.close();
console.log(JSON.stringify({first,second,third,sameWrappers,timers,result,sameReceiver,sameArguments,
  health,restored,dormant,ordering,registrations:fake.registrations.length}));
''')
        self.assertTrue(all(value[key]['api'] for key in ('first', 'second', 'third')))
        self.assertTrue(all(value[key] for key in ('sameWrappers', 'sameReceiver', 'sameArguments', 'restored', 'dormant')))
        self.assertEqual(value['result'], 'native-PlayProgress')
        self.assertEqual(value['timers'], 2)
        self.assertEqual(value['registrations'], 0)
        self.assertTrue(value['health']['chainIntact'])
        self.assertEqual(value['health']['originalCalls'], dict(PlayProgress=1, PlayState=1, Load=1, End=1))
        self.assertEqual(value['ordering'][0], ['earlier', 'PlayProgress'])

    @unittest.skipUnless(shutil.which('node'), 'optional existing Node runtime unavailable')
    def test_unreachable_native_owner_refuses_without_displacement(self):
        value = self.run_javascript(r'''
const slots = {...fake.nativeSlots};
const probe = eval(SCRIPT);
for (const event of fake.events) registered[event]('song', 1, .1);
console.log(JSON.stringify({probe, nativeCalls:fake.calls.length, registrations:fake.registrations.length,
  unchanged:fake.events.every(event => fake.nativeSlots['audioplayer.on'+event] === slots['audioplayer.on'+event]),
  keys:Object.keys(legacyNativeCmder.registerCallMap), installed:!!globalThis.__gluspotNeteaseV1}));
''', existing=False)
        self.assertEqual(value['probe'], dict(api=False, reason='native-callback-chain-unavailable'))
        self.assertEqual(value['nativeCalls'], 4)
        self.assertTrue(value['unchanged'])
        self.assertEqual(value['registrations'], 0)
        self.assertEqual(value['keys'], [])
        self.assertFalse(value['installed'])

    @unittest.skipUnless(shutil.which('node'), 'optional existing Node runtime unavailable')
    def test_all_events_preflight_before_mutation_and_partial_install_rolls_back(self):
        for mode in ('missing-end', 'empty-end', 'unwritable-end', 'accessor-end', 'missing-stub', 'write-failure'):
            with self.subTest(mode=mode):
                value = self.run_javascript(r'''
const mode = MODE, map = legacyNativeCmder.registerCallMap;
if (mode === 'missing-end') delete map['audioplayer.onEnd'];
if (mode === 'empty-end') map['audioplayer.onEnd'] = [];
if (mode === 'unwritable-end') Object.freeze(map['audioplayer.onEnd']);
if (mode === 'accessor-end') Object.defineProperty(map, 'audioplayer.onEnd', {get() { throw Error('no getter'); }});
if (mode === 'missing-stub') delete legacyNativeCmder.registerCallStubs['audioplayer.onEnd'];
if (mode === 'write-failure') map['audioplayer.onEnd'] = new Proxy(map['audioplayer.onEnd'], {
  defineProperty() { throw Error('blocked'); }
});
const probe = eval(SCRIPT);
const untouched = ['PlayProgress','PlayState','Load'].every(event => map['audioplayer.on'+event][0] === fake.originals[event]);
console.log(JSON.stringify({probe,untouched,registrations:fake.registrations.length,installed:!!globalThis.__gluspotNeteaseV1}));
'''.replace('MODE', json.dumps(mode)))
                self.assertEqual(value['probe'], dict(api=False, reason='native-callback-chain-unavailable'))
                self.assertTrue(value['untouched'])
                self.assertEqual(value['registrations'], 0)
                self.assertFalse(value['installed'])

    @unittest.skipUnless(shutil.which('node'), 'optional existing Node runtime unavailable')
    def test_observer_errors_cannot_change_original_return_or_exception(self):
        value = self.run_javascript(r'''
globalThis.setInterval = () => 1; globalThis.clearInterval = () => {};
globalThis.performance = {now() { throw Error('GluSpot clock failure'); }};
globalThis.__gluspotNeteaseSample = () => { throw Error('GluSpot binding failure'); };
const nativeError = new Error('native exception');
legacyNativeCmder.registerCallMap['audioplayer.onEnd'][0] = () => { throw nativeError; };
eval(SCRIPT);
const result = registered.PlayProgress('song', 5, .1);
let sameError = false;
try { registered.End(); } catch (error) { sameError = error === nativeError; }
globalThis.__gluspotNeteaseV1.close();
console.log(JSON.stringify({result,sameError,nativeCalls:fake.calls.length}));
''')
        self.assertEqual(value, dict(result='native-PlayProgress', sameError=True, nativeCalls=1))

    @unittest.skipUnless(shutil.which('node'), 'optional existing Node runtime unavailable')
    def test_strict_original_preserves_null_undefined_and_primitive_receivers(self):
        value = self.run_javascript(r'''
globalThis.setInterval = () => 1; globalThis.clearInterval = () => {};
globalThis.__gluspotNeteaseSample = () => {};
const queue = legacyNativeCmder.registerCallMap['audioplayer.onPlayProgress'];
queue[0] = function() { 'use strict'; return this; };
eval(SCRIPT);
const preserved = [undefined, null, 7, 'receiver', false].every(receiver =>
  Reflect.apply(queue[0], receiver, ['song',1,.1]) === receiver);
globalThis.__gluspotNeteaseV1.close();
console.log(JSON.stringify({preserved}));
''')
        self.assertTrue(value['preserved'])

    @unittest.skipUnless(shutil.which('node'), 'optional existing Node runtime unavailable')
    def test_player_replacement_is_preserved_and_chain_loss_closes_other_hooks(self):
        value = self.run_javascript(r'''
let tick;
const pushed = [];
globalThis.setInterval = callback => { tick = callback; return 1; };
globalThis.clearInterval = () => {};
globalThis.__gluspotNeteaseSample = value => pushed.push(JSON.parse(value));
eval(SCRIPT);
const adapter = globalThis.__gluspotNeteaseV1;
const replacement = () => 'replacement';
legacyNativeCmder.registerCallMap['audioplayer.onEnd'][0] = replacement;
tick();
console.log(JSON.stringify({health:adapter.health(), pushed,
  preserved:legacyNativeCmder.registerCallMap['audioplayer.onEnd'][0] === replacement,
  restored:legacyNativeCmder.registerCallMap['audioplayer.onPlayProgress'][0] === fake.originals.PlayProgress}));
''')
        self.assertFalse(value['health']['chainIntact'])
        self.assertEqual(value['pushed'], [dict(type='unavailable', reason='native-callback-chain-changed')])
        self.assertTrue(value['preserved'] and value['restored'])

    @unittest.skipUnless(shutil.which('node'), 'optional existing Node runtime unavailable')
    def test_old_unsafe_adapter_is_never_restarted(self):
        value = self.run_javascript(r'''
globalThis.__gluspotNeteaseV1 = {start() { throw Error('must not run'); }};
console.log(JSON.stringify(eval(SCRIPT)));
''')
        self.assertEqual(value, dict(api=False, reason='unsafe-previous-adapter'))

    @unittest.skipUnless(shutil.which('node'), 'optional existing Node runtime unavailable')
    def test_unwritable_adapter_global_rolls_back_all_hooks(self):
        value = self.run_javascript(r'''
globalThis.setInterval = () => 1; globalThis.clearInterval = () => {};
Object.defineProperty(globalThis, '__gluspotNeteaseV1', {value:undefined,configurable:false});
const probe = eval(SCRIPT);
console.log(JSON.stringify({probe,restored:fake.events.every(event =>
  legacyNativeCmder.registerCallMap['audioplayer.on'+event][0] === fake.originals[event])}));
''')
        self.assertEqual(value['probe'], dict(api=False, reason='native-callback-chain-unavailable'))
        self.assertTrue(value['restored'])

    @unittest.skipUnless(shutil.which('node'), 'optional existing Node runtime unavailable')
    def test_real_string_track_progress_and_zero_ratio(self):
        harness = r"""
const seeks = [];
let now = 0;
globalThis.performance = {now: () => now};
globalThis.setInterval = () => 1; globalThis.clearInterval = () => {};
globalThis.__gluspotNeteaseSample = () => {};
const channel = {call: (method, callback, args) => seeks.push(args)};
eval(SCRIPT);
registered.Load({duration_seconds: 38});
registered.PlayProgress('1390072207_BRKMZZ', 0, 0);
const initial = globalThis.__gluspotNeteaseV1.read();
now = 100; registered.PlayProgress('1390072207_BRKMZZ', 14.046, 0.36996);
const progress = globalThis.__gluspotNeteaseV1.read();
globalThis.__gluspotNeteaseV1.seek('1390072207_BRKMZZ', 'req', 20);
now = 200; registered.PlayProgress('1390072207_BRKMZZ', 20, 0.5268);
const readback = globalThis.__gluspotNeteaseV1.read();
registered.Load();
registered.PlayProgress('1390072207_BRKMZZ', 0, 0);
const unknown = globalThis.__gluspotNeteaseV1.read();
console.log(JSON.stringify({initial, progress, readback, unknown, seeks}));
"""
        value = self.run_javascript(harness)
        self.assertEqual(value['initial']['duration'], 38)
        self.assertEqual(value['unknown']['duration'], 0)
        self.assertAlmostEqual(value['progress']['duration'], 14.046 / .36996)
        self.assertEqual(value['readback']['trackId'], value['progress']['trackId'])
        self.assertEqual(value['seeks'], [['1390072207_BRKMZZ', 'req', 20]])

    @unittest.skipUnless(shutil.which('node'), 'optional existing Node runtime unavailable')
    def test_ratio_one_progress_survives_multiple_seeks_and_cdp_validation(self):
        harness = r"""
const pushed = [];
let now = 0;
globalThis.performance = {now: () => now};
globalThis.setInterval = () => 1; globalThis.clearInterval = () => {};
globalThis.__gluspotNeteaseSample = value => pushed.push(JSON.parse(value));
const channel = {call: () => {}};
eval(SCRIPT);
registered.PlayProgress('3444775814_PL8SGJ', 43.238, 1);
for (const target of [15, 45, 90]) {
  globalThis.__gluspotNeteaseV1.seek('3444775814_PL8SGJ', 'req', target);
  now += 100; registered.PlayProgress('3444775814_PL8SGJ', target, 1);
  now += 100; registered.PlayProgress('3444775814_PL8SGJ', target + 0.1, 1);
}
registered.Load({duration_seconds: 200});
registered.PlayProgress('3444775814_PL8SGJ', 43.238, 1);
console.log(JSON.stringify(pushed));
"""
        pushed = self.run_javascript(harness)
        samples = [value for value in pushed if value['type'] == 'sample']
        self.assertEqual([sample['duration'] for sample in samples], [0] * 7 + [200])
        session = CdpSession(12345)
        for sample in samples[:7]:
            session._sample(sample)
            self.assertEqual(session.latest['sequence'], sample['sequence'])
            self.assertEqual(session.latest['position'], sample['position'])

    @unittest.skipUnless(shutil.which('node'), 'optional existing Node runtime unavailable')
    def test_real_script_native_events_coalesce_and_seek_without_optimism(self):
        harness = r'''
const pushed = [], seeks = [];
let now = 0;
globalThis.performance = {now: () => now};
globalThis.setInterval = () => 123; globalThis.clearInterval = () => {};
globalThis.__gluspotNeteaseSample = value => pushed.push(JSON.parse(value));
const channel = {call:(method,callback,args) => seeks.push([method,args])};
const probe = eval(SCRIPT);
for (now of [0,30,60,100]) registered.PlayProgress(42, now/1000, (now/1000)/180);
const before = globalThis.__gluspotNeteaseV1.read().position;
globalThis.__gluspotNeteaseV1.seek('42','seek-id',90);
const after = globalThis.__gluspotNeteaseV1.read().position;
now = 130; registered.PlayProgress(42,90,0.5);
registered.PlayState(42, false);
registered.Load();
const beforeCloseCount = pushed.length;
globalThis.__gluspotNeteaseV1.close();
registered.PlayProgress(99,40,0.5); registered.PlayState(99,true);
const dormant = {count:pushed.length,sample:globalThis.__gluspotNeteaseV1.read()};
globalThis.__gluspotNeteaseV1.start();
registered.PlayProgress(99,5,0.1);
const restarted = globalThis.__gluspotNeteaseV1.read();
console.log(JSON.stringify({probe,before,after,pushed:pushed.slice(0,beforeCloseCount),
                           seeks,events:Object.keys(registered),beforeCloseCount,dormant,restarted}));
'''
        value = self.run_javascript(harness)
        self.assertEqual(value['events'], ['PlayProgress','PlayState','Load','End'])
        self.assertEqual(value['before'], value['after'])
        samples = [p for p in value['pushed'] if p['type'] == 'sample']
        self.assertEqual(len(samples), 4)  # 0ms, 100ms, immediate seek, state
        self.assertEqual(samples[2]['duration'], 180)
        self.assertFalse(samples[3]['playing'])
        self.assertEqual(value['seeks'], [['audioplayer.seek',[42,'seek-id',90]]])
        self.assertEqual(value['pushed'][-1], {'type':'reset'})
        self.assertEqual(value['dormant'], {'count':value['beforeCloseCount'],'sample':None})
        self.assertEqual(value['restarted']['trackId'], '99')
        self.assertEqual(value['restarted']['position'], 5)


class WebSocketFrameTests(CaptureAsyncTestCase):
    def connection(self, data):
        from api.capture._websocket import LoopbackWebSocket
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        class Writer:
            def __init__(self):
                self.data = bytearray()
            def write(self, data):
                self.data.extend(data)
            async def drain(self):
                pass
        writer = Writer()
        return LoopbackWebSocket(reader, writer), writer

    async def test_fragmented_text_with_interleaved_ping(self):
        connection, writer = self.connection(b'\x01\x02he\x89\x01?\x80\x03llo')
        self.assertEqual(await connection.receive(), 'hello')
        self.assertEqual(writer.data[0], 0x8a)
        self.assertTrue(writer.data[1] & 0x80)

    async def test_rejects_masked_server_frame(self):
        from api.capture._websocket import WebSocketError
        connection, _ = self.connection(b'\x81\x80')
        with self.assertRaises(WebSocketError):
            await connection.receive()

    async def test_rejects_binary_message(self):
        from api.capture._websocket import WebSocketError
        connection, _ = self.connection(b'\x82\x01x')
        with self.assertRaises(WebSocketError):
            await connection.receive()

    async def test_rejects_oversized_payload_before_reading_it(self):
        import struct
        from api.capture._websocket import WebSocketError
        connection, _ = self.connection(b'\x81\x7f' + struct.pack('!Q', 5 * 1024 * 1024))
        with self.assertRaises(WebSocketError):
            await connection.receive()


class BoundedIoTests(CaptureAsyncTestCase):
    async def test_fixture_close_handles_reset_and_stalled_writer(self):
        class Writer:
            def __init__(self, reset):
                self.reset = reset
                self.closed = False

            def close(self):
                self.closed = True

            async def wait_closed(self):
                if self.reset:
                    raise ConnectionResetError("connection reset")
                await asyncio.Future()

        reset, stalled = Writer(True), Writer(False)
        await asyncio.wait_for(asyncio.gather(close_writer(reset), close_writer(stalled)), 2)
        self.assertTrue(reset.closed and stalled.closed)

    async def test_fixture_stop_cancels_incomplete_client_handshake(self):
        server = await FakeCdpServer().__aenter__()
        reader, writer = await asyncio.open_connection('127.0.0.1', server.port)
        try:
            writer.write(b'GET /')
            await writer.drain()
            await asyncio.sleep(0.01)
            self.assertTrue(server.tasks)
            await asyncio.wait_for(server.close(), 2)
            self.assertFalse(server.tasks)
            self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')
        finally:
            await close_writer(writer)
            await server.close()

    async def test_command_timeout_includes_socket_drain(self):
        class StalledSocket:
            async def send(self, _):
                await asyncio.Future()
        session = CdpSession(1234, command_timeout=0.02)
        session._ws = StalledSocket()
        started = asyncio.get_running_loop().time()
        with self.assertRaises(asyncio.TimeoutError):
            await session.command('Runtime.enable')
        self.assertLess(asyncio.get_running_loop().time() - started, 0.2)
        self.assertEqual(session._pending, {})
        session._ws = None
        await session.close()

    async def test_close_aborts_when_drain_and_shutdown_stall(self):
        from api.capture._websocket import LoopbackWebSocket
        class StalledWriter:
            def __init__(self):
                self.closed = self.aborted = False
                self.transport = self
            def is_closing(self):
                return self.closed
            def write(self, _):
                pass
            async def drain(self):
                await asyncio.Future()
            def close(self):
                self.closed = True
            async def wait_closed(self):
                await asyncio.Future()
            def abort(self):
                self.aborted = True
        writer = StalledWriter()
        connection = LoopbackWebSocket(None, writer)
        connection.CLOSE_TIMEOUT = 0.01
        await asyncio.wait_for(connection.close(), 0.1)
        self.assertTrue(writer.closed)
        self.assertTrue(writer.aborted)

    async def test_discovery_reads_segmented_body_without_content_length(self):
        async def respond(reader, writer):
            await reader.readuntil(b'\r\n\r\n')
            body = json.dumps([dict(type='page',url='orpheus://orpheus/pub/app.html',
                                   webSocketDebuggerUrl=f'ws://127.0.0.1:{port}/devtools/page/1')]).encode()
            writer.write(b'HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n' + body[:8])
            await writer.drain()
            await asyncio.sleep(0.01)
            writer.write(body[8:])
            await writer.drain()
            await close_writer(writer)
        server = await asyncio.start_server(respond, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        try:
            target = await discover_target(port)
            self.assertEqual(target['url'], 'orpheus://orpheus/pub/app.html')
        finally:
            server.close()
            await asyncio.wait_for(server.wait_closed(), 1)
