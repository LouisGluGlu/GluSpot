"""Stdlib loopback HTTP/CDP/WebSocket fixture shared by capture tests."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import struct

from api.capture.netease_js import EARLY_REVISION


async def close_writer(writer):
    try:
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), 1)
    except (OSError, asyncio.TimeoutError):
        pass


class FakeCdpServer:
    def __init__(self):
        self.port = 0
        self.server = None
        self.clients = set()
        self.tasks = set()
        self.commands = []
        self.connections = 0
        self.sample = dict(type='sample', trackId='native-1', position=12.0,
                           duration=180.0, playing=True, rate=1.0,
                           event='PlayProgress', sequence=1)
        self.reject_probe = False
        self.target_url = 'orpheus://orpheus/pub/app.html'
        self.target_host = '127.0.0.1'
        self.send_heartbeats = True
        self.new_document_scripts = {}
        self.reloads = 0
        self.reload_context_events = True
        self.require_early_shim = False
        self.early_shim_active = False
        self.early_shim_revision = None

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._accept, '127.0.0.1', 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def close(self):
        if self.server:
            self.server.close()
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await self.drop_connections()
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)
        if self.server:
            await asyncio.wait_for(self.server.wait_closed(), 1)

    async def drop_connections(self):
        clients = tuple(self.clients)
        await asyncio.gather(*(close_writer(writer) for writer in clients))

    async def emit(self, payload):
        message = dict(method='Runtime.bindingCalled', params=dict(
            name='__gluspotNeteaseSample', executionContextId=1,
            payload=json.dumps(payload)))
        for writer in tuple(self.clients):
            await self._send(writer, message)

    async def _send(self, writer, message):
        payload = json.dumps(message).encode()
        length = bytes([len(payload)]) if len(payload) < 126 else b'\x7e' + struct.pack('!H', len(payload))
        writer.write(b'\x81' + length + payload)
        await writer.drain()

    async def _read(self, reader):
        first, second = await reader.readexactly(2)
        if first & 15 == 8:
            raise ConnectionError('closed')
        assert second & 128, 'client must mask frames'
        length = second & 127
        if length == 126:
            length = struct.unpack('!H', await reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack('!Q', await reader.readexactly(8))[0]
        mask = await reader.readexactly(4)
        payload = await reader.readexactly(length)
        return json.loads(bytes(value ^ mask[i % 4] for i, value in enumerate(payload)))

    async def _heartbeat(self):
        while True:
            await asyncio.sleep(0.05)
            if self.send_heartbeats:
                await self.emit({'type':'heartbeat'})

    async def _accept(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        heartbeat = None
        try:
            data = (await reader.readuntil(b'\r\n\r\n')).decode('ascii')
            if data.startswith('GET /json '):
                targets = [dict(id='unrelated', url='about:blank', type='page'),
                           dict(id='player', url=self.target_url, type='page',
                                webSocketDebuggerUrl=f'ws://{self.target_host}:{self.port}/devtools/page/player')]
                body = json.dumps(targets).encode()
                writer.write(f'HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n'.encode() + body)
                await writer.drain()
                return
            key = re.search(r'Sec-WebSocket-Key: ([^\r]+)', data).group(1)
            accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            writer.write(f'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n'.encode())
            await writer.drain()
            self.clients.add(writer)
            self.connections += 1
            heartbeat = asyncio.create_task(self._heartbeat())
            while True:
                message = await self._read(reader)
                self.commands.append(message)
                method, params = message['method'], message.get('params', {})
                result = {}
                emit = False
                reloaded = False
                if method == 'Page.addScriptToEvaluateOnNewDocument':
                    identifier = f'script-{len(self.new_document_scripts) + 1}'
                    self.new_document_scripts[identifier] = params['source']
                    result = dict(identifier=identifier)
                elif method == 'Page.removeScriptToEvaluateOnNewDocument':
                    self.new_document_scripts.pop(params['identifier'], None)
                elif method == 'Page.reload':
                    self.reloads += 1
                    self.early_shim_active = bool(self.new_document_scripts)
                    self.early_shim_revision = EARLY_REVISION if self.early_shim_active else None
                    reloaded = self.reload_context_events
                elif method == 'Runtime.enable':
                    await self._send(writer, dict(method='Runtime.executionContextCreated',params={'context':{'id':1}}))
                elif method == 'Runtime.evaluate':
                    expression = params.get('expression', '')
                    if expression.startswith('Boolean(globalThis.__gluspotNeteaseEarlyV1'):
                        revision = re.search(r'\.revision === (\d+)', expression)
                        compatible = not revision or self.early_shim_revision == int(revision.group(1))
                        result = {'result': {'value': self.early_shim_active and compatible}}
                    elif '.seek(' in expression:
                        arguments = json.loads('[' + expression.split('.seek(', 1)[1][:-1] + ']')
                        self.sample = dict(self.sample, position=arguments[2], sequence=self.sample['sequence'] + 1)
                        result = {'result': {'value': True}}
                        emit = True
                    elif expression.endswith('.read()'):
                        result = {'result': {'value': self.sample}}
                    elif expression.endswith('.health()'):
                        result = {'result': {'value': dict(chainIntact=True,
                            originalCalls=dict(PlayProgress=self.sample['sequence'], PlayState=0, Load=0, End=0))}}
                    elif 'native-events-unavailable' in expression:
                        rejected = self.reject_probe or (self.require_early_shim and not self.early_shim_active)
                        result = {'result': {'value': {'api': not rejected, 'sampleHz':10,
                                                     'seek':True,'seekWhilePaused':None,
                                                     'adapter':'legacy-native-v1',
                                                     'hook':('load-time-native-chain-v1' if self.early_shim_active
                                                             else 'existing-websdk-chain-v1'),
                                                     **({'reason':'native-callback-chain-unavailable'} if rejected else {})}}}
                        emit = not rejected
                    else:
                        result = {'result': {'value': None}}
                await self._send(writer, dict(id=message['id'], result=result))
                if reloaded:
                    await self._send(writer, dict(method='Runtime.executionContextsCleared'))
                    await self._send(writer, dict(method='Runtime.executionContextCreated',
                                                 params={'context': {'id': self.reloads + 1}}))
                    await self._send(writer, dict(method='Page.loadEventFired', params={'timestamp': 0}))
                if emit:
                    await self.emit(self.sample)
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            if heartbeat:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            self.clients.discard(writer)
            await close_writer(writer)
            self.tasks.discard(task)
