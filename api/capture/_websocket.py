"""Small RFC 6455 client for loopback CDP; Python standard library only.

Written for GluSpot, not a general WebSocket dependency. Supports uncompressed
UTF-8 text, fragmentation, ping/pong and close; rejects masked server frames,
extensions, binary messages and messages over 4 MiB. The session owns one reader;
a lock serializes every client frame, including control frames. No TLS, proxy,
redirect, authentication or arbitrary remote endpoints are supported.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import struct


class WebSocketError(ConnectionError):
    pass


class LoopbackWebSocket:
    MAX_MESSAGE = 4 * 1024 * 1024
    CLOSE_TIMEOUT = 0.25

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self._write_lock = asyncio.Lock()

    @classmethod
    async def connect(cls, port: int, path: str):
        if not path.startswith('/') or '\r' in path or '\n' in path:
            raise ValueError('invalid CDP WebSocket path')
        reader, writer = await asyncio.open_connection('127.0.0.1', port, limit=65536)
        try:
            key = base64.b64encode(os.urandom(16)).decode('ascii')
            writer.write((f'GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
                          'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                          f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode('ascii'))
            await writer.drain()
            header = await reader.readuntil(b'\r\n\r\n')
            lines = header.decode('iso-8859-1').split('\r\n')
            headers = {line.split(':', 1)[0].lower(): line.split(':', 1)[1].strip()
                       for line in lines[1:] if ':' in line}
            expected = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode('ascii')).digest()).decode('ascii')
            if (lines[0].split()[1:2] != ['101'] or headers.get('sec-websocket-accept') != expected
                    or headers.get('upgrade', '').lower() != 'websocket'
                    or 'upgrade' not in [token.strip() for token in headers.get('connection', '').lower().split(',')]
                    or headers.get('sec-websocket-extensions')):
                raise WebSocketError('invalid CDP WebSocket handshake')
            return cls(reader, writer)
        except BaseException:
            writer.close()
            await writer.wait_closed()
            raise

    async def send(self, text: str):
        await self._send_frame(1, text.encode('utf-8'))

    async def _send_frame(self, opcode, payload):
        if len(payload) > self.MAX_MESSAGE:
            raise WebSocketError('CDP message too large')
        mask = os.urandom(4)
        size = len(payload)
        length = bytes((0x80 | size,)) if size < 126 else (b'\xfe' + struct.pack('!H', size) if size < 65536 else b'\xff' + struct.pack('!Q', size))
        frame = bytes((0x80 | opcode,)) + length + mask + bytes(v ^ mask[i % 4] for i, v in enumerate(payload))
        async with self._write_lock:
            self.writer.write(frame)
            await self.writer.drain()

    async def receive(self) -> str:
        parts, total, fragmented = [], 0, False
        while True:
            first, second = await self.reader.readexactly(2)
            final, opcode = bool(first & 0x80), first & 15
            if first & 0x70 or second & 0x80:
                raise WebSocketError('unsupported CDP WebSocket frame')
            size = second & 127
            if size == 126:
                size = struct.unpack('!H', await self.reader.readexactly(2))[0]
            elif size == 127:
                size = struct.unpack('!Q', await self.reader.readexactly(8))[0]
            if size > self.MAX_MESSAGE or (opcode >= 8 and (not final or size > 125)):
                raise WebSocketError('invalid CDP WebSocket frame size')
            payload = await self.reader.readexactly(size)
            if opcode == 8:
                raise WebSocketError('CDP WebSocket closed')
            if opcode == 9:
                await self._send_frame(10, payload)
                continue
            if opcode == 10:
                continue
            if opcode not in (0, 1) or (opcode == 0) != fragmented:
                raise WebSocketError('unsupported CDP WebSocket message')
            parts.append(payload)
            total += size
            if total > self.MAX_MESSAGE:
                raise WebSocketError('CDP message too large')
            if final:
                return b''.join(parts).decode('utf-8')
            fragmented = True

    async def close(self):
        if not self.writer.is_closing():
            try:
                await asyncio.wait_for(self._send_frame(8, b''), self.CLOSE_TIMEOUT)
            except (ConnectionError, OSError, asyncio.TimeoutError):
                pass
            finally:
                self.writer.close()
            try:
                await asyncio.wait_for(self.writer.wait_closed(), self.CLOSE_TIMEOUT)
            except (ConnectionError, OSError, asyncio.TimeoutError):
                self.writer.transport.abort()
