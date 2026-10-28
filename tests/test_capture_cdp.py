import asyncio
import json
import math
import os
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
        sample = self.session.latest
        self.assertEqual(sample['position'], 12)
        readback = await self.session.readback()
        self.assertEqual(sample, readback)
        self.assertEqual(self.session.samples, 1)
        self.assertEqual([m['method'] for m in self.server.commands][:3],
                         ['Runtime.enable', 'Runtime.addBinding', 'Runtime.evaluate'])

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

    async def test_probe_failure_never_becomes_ready(self):
        self.server.reject_probe = True
        await self.session.start()
        self.assertFalse(await self.session.wait_ready(timeout=0.08))
        self.assertFalse(await self.session.seek('native-1', 'no', 3))

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
    @unittest.skipUnless(shutil.which('node'), 'optional existing Node runtime unavailable')
    def test_real_string_track_progress_and_zero_ratio(self):
        script = JsAdapterRegistry.select().install_expression()
        harness = r"""
const registered = {}, seeks = [];
let now = 0;
globalThis.performance = {now: () => now};
globalThis.setInterval = () => 1; globalThis.clearInterval = () => {};
globalThis.__gluspotNeteaseSample = () => {};
const legacyNativeCmder = {appendRegisterCall: (name, source, callback) => registered[name] = callback};
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
""".replace('SCRIPT', json.dumps(script))
        value = json.loads(subprocess.run([shutil.which('node'), '-e', harness],
                                          check=True, text=True, capture_output=True).stdout)
        self.assertEqual(value['initial']['duration'], 38)
        self.assertEqual(value['unknown']['duration'], 0)
        self.assertAlmostEqual(value['progress']['duration'], 14.046 / .36996)
        self.assertEqual(value['readback']['trackId'], value['progress']['trackId'])
        self.assertEqual(value['seeks'], [['1390072207_BRKMZZ', 'req', 20]])

    @unittest.skipUnless(shutil.which('node'), 'optional existing Node runtime unavailable')
    def test_real_script_native_events_coalesce_and_seek_without_optimism(self):
        script = JsAdapterRegistry.select().install_expression()
        harness = r'''
const registered = {}, pushed = [], seeks = [];
let now = 0;
globalThis.performance = {now: () => now};
globalThis.setInterval = () => 123; globalThis.clearInterval = () => {};
globalThis.__gluspotNeteaseSample = value => pushed.push(JSON.parse(value));
const legacyNativeCmder = {appendRegisterCall: (name, source, callback) => {registered[name] = callback;}};
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
'''.replace('SCRIPT', json.dumps(script))
        result = subprocess.run([shutil.which('node'), '-e', harness], check=True, text=True, capture_output=True)
        value = json.loads(result.stdout)
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
