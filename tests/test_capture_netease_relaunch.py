"""Isolated relaunch and recovery regressions; no real player is touched."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api.capture.netease import NetEaseInstallation, NetEaseLocator, _RelaunchOwner


PATH = '/virtual/NetEase/cloudmusic.exe'
OTHER = '/virtual/other.exe'


class Player:
    def __init__(self):
        self.running = [(1, PATH)]
        self.ports = []
        self.stopped = []
        self.spawns = []
        self.next_pid = 10
        self.lock = threading.RLock()
        self.stop_hook = None
        self.spawn_hook = None
        self.health = True

    def processes(self):
        with self.lock:
            return list(self.running)

    def debug_ports(self):
        with self.lock:
            return list(self.ports)

    def stop(self, pid):
        with self.lock:
            self.stopped.append(pid)
            if self.stop_hook:
                self.stop_hook(pid)
            self.running = [(p, path) for p, path in self.running if p != pid]
            self.ports = [(p, path, port) for p, path, port in self.ports if p != pid]

    def spawn(self, args, **kwargs):
        with self.lock:
            self.spawns.append(list(args))
            if self.spawn_hook:
                self.spawn_hook(args)
            pid = self.next_pid
            self.next_pid += 1
            self.running.append((pid, args[0]))
            for arg in args[1:]:
                if arg.startswith('--remote-debugging-port='):
                    self.ports.append((pid, args[0], int(arg.split('=', 1)[1])))
            return SimpleNamespace(pid=pid)

    def healthy(self, port):
        return self.health


class NetEaseRelaunchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.player = Player()
        self.installation = NetEaseInstallation(PATH, '1', (1,))

    def locator(self, **overrides):
        options = dict(processes=self.player.processes, registry_paths=lambda: [],
                       exists=lambda path: path == PATH, version=lambda path: '1',
                       spawn=self.player.spawn, stop=self.player.stop,
                       owner_dir=Path(self.temp.name), debug_ports=self.player.debug_ports,
                       healthy=self.player.healthy, clock=lambda: 1000.0,
                       sleep=time.sleep, startup_timeout=0.15)
        options.update(overrides)
        return NetEaseLocator(PATH, **options)

    def assert_normal_started(self):
        self.assertTrue(any(args == [PATH] for args in self.player.spawns), self.player.spawns)
        self.assertTrue(any(path == PATH for _, path in self.player.processes()))

    async def test_partial_stop_failure_restores_normal_player(self):
        self.player.running.append((2, PATH))
        def fail_second(pid):
            if pid == 2:
                raise OSError('stop failed')
        self.player.stop_hook = fail_second
        try:
            await self.locator().launch(self.installation, relaunch=True)
        except (OSError, RuntimeError):
            pass
        self.assert_normal_started()

    async def test_debug_spawn_failure_restores_normal_player(self):
        def fail_debug(args):
            if len(args) > 1:
                raise OSError('spawn failed')
        self.player.spawn_hook = fail_debug
        try:
            await self.locator().launch(self.installation, relaunch=True)
        except (OSError, RuntimeError):
            pass
        self.assert_normal_started()

    async def test_normal_recovery_failure_is_surfaced(self):
        def fail_spawn(args):
            raise OSError('debug spawn failed' if len(args) > 1 else 'normal recovery failed')
        self.player.spawn_hook = fail_spawn
        with self.assertRaisesRegex(OSError, 'normal recovery failed'):
            await self.locator().launch(self.installation, relaunch=True)
        self.assertEqual(len(self.player.spawns), 2)
        self.assertEqual(self.player.spawns[-1], [PATH])
        self.assertEqual(self.player.processes(), [])

    async def test_unhealthy_debug_port_restores_normal_player(self):
        self.player.health = False
        try:
            await self.locator().launch(self.installation, relaunch=True)
        except (OSError, RuntimeError, TimeoutError):
            pass
        self.assert_normal_started()

    async def test_child_exit_restores_normal_player(self):
        original = self.player.spawn
        def exited(args, **kwargs):
            process = original(args, **kwargs)
            if len(args) > 1:
                with self.player.lock:
                    self.player.running = [(p, path) for p, path in self.player.running if p != process.pid]
                    self.player.ports = []
            return process
        try:
            await self.locator(spawn=exited).launch(self.installation, relaunch=True)
        except (OSError, RuntimeError, TimeoutError):
            pass
        self.assert_normal_started()

    async def test_concurrent_locators_share_one_relaunch(self):
        first, second = self.locator(), self.locator()
        a, b = await asyncio.wait_for(asyncio.gather(
            first.launch(self.installation, relaunch=True),
            second.launch(self.installation, relaunch=True)), 5)
        self.assertEqual(self.player.stopped, [1])
        self.assertEqual(a.port, b.port)
        self.assertTrue(a.reused or b.reused)

    async def test_persisted_cooldown_blocks_second_unhealthy_relaunch(self):
        await self.locator().launch(self.installation, relaunch=True)
        self.player.health = False
        before = list(self.player.stopped)
        try:
            await self.locator().launch(self.installation, relaunch=True)
        except (OSError, RuntimeError, TimeoutError):
            pass
        self.assertEqual(self.player.stopped, before)

    async def test_failed_state_update_preserves_reserved_cooldown(self):
        import os
        replace = os.replace
        count = 0
        def fail_after_reservation(source, target):
            nonlocal count
            count += 1
            if count > 1:
                raise OSError('interrupted phase update')
            replace(source, target)
        with patch('api.capture.netease.os.replace', side_effect=fail_after_reservation):
            with self.assertRaises(OSError):
                await self.locator().launch(self.installation, relaunch=True)
        state = json.loads(next(Path(self.temp.name).glob('*.json')).read_text())
        self.assertEqual(state['attempted_at'], 1000.0)
        self.assert_normal_started()
        self.player.health = False
        with self.assertRaisesRegex(RuntimeError, 'cooldown'):
            await self.locator().launch(self.installation, relaunch=True)
        self.assertEqual(self.player.stopped, [1])

    async def test_owner_lock_excludes_an_independent_process(self):
        # Exercise the OS lock on the platform running this suite, including
        # Windows, with an independent interpreter rather than another task.
        script = """
import sys
handle = open(sys.argv[1], 'r+b')
try:
    if sys.platform == 'win32':
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    print('blocked')
else:
    print('acquired')
finally:
    handle.close()
"""
        owner = _RelaunchOwner(Path(self.temp.name), PATH, None)
        def attempt():
            return subprocess.run([sys.executable, '-c', script, str(owner.path)],
                                  capture_output=True, text=True, timeout=3, check=True).stdout.strip()
        with owner:
            self.assertEqual(await asyncio.to_thread(attempt), 'blocked')
        self.assertEqual(await asyncio.to_thread(attempt), 'acquired')

    async def test_existing_healthy_debug_port_is_reused_without_relaunch(self):
        self.player.ports = [(1, PATH, 19222)]
        result = await self.locator().launch(self.installation)
        self.assertTrue(result.reused)
        self.assertEqual(result.port, 19222)
        self.assertEqual(self.player.stopped, [])
        self.assertEqual(self.player.spawns, [])

    async def test_cancellation_restores_normal_player(self):
        self.player.health = False
        entered = threading.Event()
        def pause(seconds):
            entered.set()
            time.sleep(0.03)
        task = asyncio.create_task(self.locator(sleep=pause).launch(self.installation, relaunch=True))
        self.assertTrue(await asyncio.to_thread(entered.wait, 3))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        self.assert_normal_started()

    async def test_pid_reuse_does_not_stop_unrelated_executable(self):
        calls = 0
        def processes():
            nonlocal calls
            calls += 1
            if calls == 2:
                with self.player.lock:
                    self.player.running = [(pid, OTHER if pid == 1 else path)
                                           for pid, path in self.player.running]
            return self.player.processes()
        await self.locator(processes=processes).launch(self.installation, relaunch=True)
        self.assertNotIn(1, self.player.stopped)
        self.assertIn((1, OTHER), self.player.processes())


if __name__ == '__main__':
    unittest.main()
