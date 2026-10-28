"""Event-driven, loopback-only CDP session for transport A.

One task owns discovery, reconnect and heartbeat checks; one reader dispatches
responses/bindings, and the WebSocket serializes all writes. Reconnection clears
physical evidence and retries with exponential backoff capped at ten seconds.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from urllib.parse import urlsplit

from api.capture._websocket import LoopbackWebSocket
from api.capture.netease_js import BINDING_NAME, JsAdapterRegistry

TARGET_URL = 'orpheus://orpheus/pub/app.html'


class CdpError(ConnectionError):
    pass


async def discover_target(port: int, *, timeout: float = 2.0) -> dict:
    """Read /json directly over loopback, without proxy or redirect handling."""
    async def fetch():
        reader, writer = await asyncio.open_connection('127.0.0.1', port, limit=65536)
        try:
            writer.write(f'GET /json HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n'.encode('ascii'))
            await writer.drain()
            header = await reader.readuntil(b'\r\n\r\n')
            lines = header.decode('iso-8859-1').split('\r\n')
            if lines[0].split()[1:2] != ['200']:
                raise CdpError('CDP discovery failed')
            headers = {line.split(':', 1)[0].lower(): line.split(':', 1)[1].strip()
                       for line in lines[1:] if ':' in line}
            if headers.get('transfer-encoding', '').lower() == 'chunked':
                chunks, total = [], 0
                while True:
                    size = int((await reader.readline()).split(b';')[0], 16)
                    if not size:
                        break
                    total += size
                    if total > LoopbackWebSocket.MAX_MESSAGE:
                        raise CdpError('CDP discovery too large')
                    chunks.append(await reader.readexactly(size))
                    if await reader.readexactly(2) != b'\r\n':
                        raise CdpError('invalid discovery chunk')
                body = b''.join(chunks)
            else:
                size = int(headers.get('content-length', 0))
                if size > LoopbackWebSocket.MAX_MESSAGE:
                    raise CdpError('CDP discovery too large')
                if size:
                    body = await reader.readexactly(size)
                else:
                    chunks, total = [], 0
                    while True:
                        chunk = await reader.read(min(65536, LoopbackWebSocket.MAX_MESSAGE + 1 - total))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        total += len(chunk)
                        if total > LoopbackWebSocket.MAX_MESSAGE:
                            raise CdpError('CDP discovery too large')
                    body = b''.join(chunks)
            if len(body) > LoopbackWebSocket.MAX_MESSAGE:
                raise CdpError('CDP discovery too large')
            targets = json.loads(body)
            if not isinstance(targets, list):
                raise CdpError('invalid CDP target list')
            for target in targets:
                if (isinstance(target, dict) and target.get('url') == TARGET_URL
                        and target.get('type', 'page') == 'page'):
                    _websocket_path(target.get('webSocketDebuggerUrl', ''), port)
                    return target
            raise CdpError('NetEase player target unavailable')
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), LoopbackWebSocket.CLOSE_TIMEOUT)
            except (OSError, asyncio.TimeoutError):
                pass
    return await asyncio.wait_for(fetch(), timeout)


def _websocket_path(url: str, port: int) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != 'ws' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parsed.port != port or parsed.username or parsed.password or parsed.fragment):
        raise CdpError('CDP target is not on the selected loopback port')
    return parsed.path + (('?' + parsed.query) if parsed.query else '')


class CdpSession:
    def __init__(self, port: int, *, adapter='auto', version='', on_sample=None,
                 command_timeout=2.0, clock=time.monotonic, reconnect_delays=(0.25, 0.5, 1, 2, 4, 8, 10)):
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError('invalid CDP port')
        if not reconnect_delays or any(v < 0 or v > 10 for v in reconnect_delays):
            raise ValueError('invalid CDP reconnect schedule')
        self.port, self.clock = port, clock
        self.adapter = JsAdapterRegistry.select(version, adapter)
        self.command_timeout = command_timeout
        self.reconnect_delays = reconnect_delays
        self._subscribers = [on_sample] if on_sample else []
        self._task = self._reader_task = self._ws = None
        self._pending = {}
        self._next_id = 0
        self._ready = asyncio.Event()
        self._closed = False
        self.latest = None
        self.probe = {}
        self.generation = self.reconnects = self.samples = self.attempts = 0
        self._last_heartbeat = 0.0
        self._context_id = None
        self._contexts = []
        self._last_error = ''

    @property
    def ready(self):
        return self._ready.is_set()

    def subscribe(self, callback):
        self._subscribers.append(callback)
        def unsubscribe():
            if callback in self._subscribers:
                self._subscribers.remove(callback)
        return unsubscribe

    async def start(self):
        if self._closed:
            raise CdpError('CDP session is closed')
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def wait_ready(self, timeout=5.0):
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def command(self, method, params=None):
        if self._ws is None:
            raise CdpError('CDP is disconnected')
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        websocket = self._ws
        async def exchange():
            await websocket.send(json.dumps(dict(id=request_id, method=method, params=params or {}), allow_nan=False))
            return await future
        try:
            # Bound lock acquisition, socket drain and response together.
            return await asyncio.wait_for(exchange(), self.command_timeout)
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                # A disconnect may fail the future while send itself fails.
                # Retrieve that exception even when exchange never awaited it.
                future.exception()

    async def _evaluate(self, expression):
        params = dict(expression=expression, returnByValue=True)
        if self._context_id is not None:
            params['contextId'] = self._context_id
        result = await self.command('Runtime.evaluate', params)
        if result.get('exceptionDetails'):
            raise CdpError('NetEase adapter evaluation failed')
        return result.get('result', {}).get('value')

    async def seek(self, track_id, request_id, seconds):
        if not self.ready or not self.probe.get('seek'):
            return False
        # The adapter never copies the seek target into readback state.
        return await self._evaluate(self.adapter.seek_expression(track_id, request_id, seconds)) is True

    async def readback(self):
        if not self.ready:
            return None
        payload = await self._evaluate(self.adapter.read_expression())
        self._sample(payload)
        return self.latest

    async def _run(self):
        failures = 0
        while not self._closed:
            try:
                self.attempts += 1
                target = await discover_target(self.port, timeout=self.command_timeout)
                self._ws = await asyncio.wait_for(LoopbackWebSocket.connect(self.port, _websocket_path(target['webSocketDebuggerUrl'], self.port)), self.command_timeout)
                self.generation += 1
                self._contexts = []
                self._context_id = None
                self._reader_task = asyncio.create_task(self._reader())
                await self.command('Runtime.enable')
                await self.command('Runtime.addBinding', {'name': BINDING_NAME})
                self.probe = {}
                # Main-world evaluation first; alternate CEF contexts are feature
                # probed rather than selected by an undocumented frame name.
                for context_id in [None] + self._contexts:
                    self._context_id = context_id
                    try:
                        probe = await self._evaluate(self.adapter.install_expression())
                    except CdpError:
                        continue
                    if isinstance(probe, dict) and probe.get('api') is True:
                        self.probe = probe
                        break
                if not self.probe:
                    raise CdpError('NetEase native adapter probe failed')
                self._last_heartbeat = self.clock()
                self._ready.set()
                connected_at = self.clock()
                self._last_error = ''
                while not self._closed:
                    await asyncio.wait({self._reader_task}, timeout=1.0)
                    if self._reader_task.done():
                        self._reader_task.result()
                        raise CdpError('CDP reader stopped')
                    if self.clock() - connected_at >= 10.0:
                        failures = 0
                    if self.clock() - self._last_heartbeat > 3.0:
                        raise CdpError('NetEase adapter missed three heartbeats')
            except asyncio.CancelledError:
                break
            except Exception as exc:
                # Error classes only: never log target URLs, page state or titles.
                self._last_error = type(exc).__name__
            finally:
                await self._disconnect()
            if not self._closed:
                self.reconnects += 1
                await asyncio.sleep(self.reconnect_delays[min(failures, len(self.reconnect_delays) - 1)])
                failures += 1

    async def _reader(self):
        while True:
            message = json.loads(await self._ws.receive())
            if 'id' in message:
                future = self._pending.get(message['id'])
                if future is not None and not future.done():
                    if 'error' in message:
                        future.set_exception(CdpError('CDP command rejected'))
                    else:
                        future.set_result(message.get('result', {}))
                continue
            method, params = message.get('method'), message.get('params', {})
            if method == 'Runtime.executionContextCreated':
                context_id = params.get('context', {}).get('id')
                if isinstance(context_id, int):
                    self._contexts.append(context_id)
            elif (method == 'Runtime.executionContextsCleared'
                  or method == 'Inspector.detached'
                  or (method == 'Runtime.executionContextDestroyed'
                      and params.get('executionContextId') == self._context_id)):
                raise CdpError('NetEase page context replaced')
            elif method == 'Runtime.bindingCalled' and params.get('name') == BINDING_NAME:
                if self._context_id is not None and params.get('executionContextId') != self._context_id:
                    continue
                try:
                    payload = json.loads(params.get('payload', ''))
                except (ValueError, TypeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                kind = payload.get('type')
                if kind == 'heartbeat':
                    self._last_heartbeat = self.clock()
                elif kind == 'reset':
                    self.latest = None
                    self._notify(None)
                elif kind == 'state':
                    # PlayState/Load/End callbacks do not carry a verified
                    # position. Preserve the last physical position and its
                    # receipt timestamp while publishing the state edge
                    # immediately; PositionModel will freeze/resume from
                    # that independent anchor.
                    playing = payload.get('playing')
                    if self.latest is not None and isinstance(playing, bool):
                        if playing != self.latest.get('playing'):
                            self.latest = dict(self.latest, playing=playing,
                                               event=str(payload.get('event', 'PlayState')),
                                               state_received_at=self.clock())
                            self._notify(self.latest)
                elif kind == 'sample':
                    self._sample(payload)

    def _sample(self, payload):
        if not isinstance(payload, dict):
            return
        try:
            track_id = payload['trackId']
            position = float(payload['position'])
            duration = float(payload.get('duration', 0))
            sequence = payload['sequence']
            if (not isinstance(track_id, str) or not track_id or isinstance(sequence, bool)
                    or not isinstance(sequence, int) or sequence < 1
                    or not math.isfinite(position) or position < 0
                    or not math.isfinite(duration) or duration < 0):
                return
        except (KeyError, ValueError, TypeError, OverflowError):
            return
        if duration and position > duration + 1.0:
            return
        previous = self.latest
        if previous is not None and previous['trackId'] == track_id:
            if sequence < previous['sequence']:
                return
            same = sequence == previous['sequence']
            if same and (position != previous['position'] or duration != previous['duration']):
                return
        else:
            same = False
        result = dict(trackId=track_id, position=position, duration=duration,
                      playing=payload.get('playing') if isinstance(payload.get('playing'), bool) else None,
                      rate=1.0, event=str(payload.get('event', 'PlayProgress')),
                      sequence=sequence, generation=self.generation,
                      received_at=previous['received_at'] if same else self.clock())
        if same:
            state_at = previous.get('state_received_at')
            if result['playing'] != previous['playing'] and isinstance(result['playing'], bool):
                state_at = self.clock()
            if state_at is not None:
                result['state_received_at'] = state_at
        self.latest = result
        if not same or result != previous:
            self.samples += int(not same)
            self._notify(result)

    def _notify(self, sample):
        for callback in tuple(self._subscribers):
            try:
                callback(sample)
            except Exception:
                # Consumer failures must not strand protocol response futures.
                pass

    async def _disconnect(self):
        self._ready.clear()
        self.latest = None
        self._notify(None)
        if self._reader_task is not None:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
            self._reader_task = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(CdpError('CDP disconnected'))
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def close(self):
        self._closed = True
        if self.ready:
            try:
                await self._evaluate(self.adapter.close_expression())
                await self.command('Runtime.removeBinding', {'name': BINDING_NAME})
            except (ConnectionError, asyncio.TimeoutError):
                pass
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        else:
            await self._disconnect()

    def diagnostics(self):
        return dict(ready=self.ready, transport='relaunch', port=self.port,
                    adapter=self.adapter.name, probe=dict(self.probe),
                    generation=self.generation, attempts=self.attempts,
                    reconnects=self.reconnects, samples=self.samples,
                    error=self._last_error)
