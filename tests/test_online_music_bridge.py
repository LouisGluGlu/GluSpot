from __future__ import annotations

import json
from contextlib import ExitStack
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from api.online_music import OnlineMusicError
from api.online_music_bridge import NodeOnlineMusicBackend


class OnlineMusicBridgeTests(unittest.TestCase):
    def test_personal_dj_uses_verified_catalog_and_request_voice_profile_without_mutating_credentials(self):
        self.write_modules("@neteasecloudmusicapienhanced/api", "4.41.1", {
            "util/option.js": "module.exports=q=>q;",
            "util/request.js": r'''module.exports=async (url,data,q)=>({body:{code:200,url,
                authorized:q.cookie.MUSIC_U==='synthetic', profile:[q.cookie.os,q.cookie.appver,q.cookie.versioncode],
                domain:q.domain,timeout:q.timeout,randomCNIP:q.randomCNIP,params:data},cookie:[]});''',
        })
        credential = {"cookie": {"MUSIC_U": "synthetic", "os": "pc", "appver": "original"}}
        response = self.backend.request("netease", "personal_dj", {"timbre_key": "fixture-voice"}, credential)
        body = response["data"]
        self.assertTrue(body["authorized"])
        self.assertEqual(body["url"], "/api/aidj/content/rcmd/info")
        self.assertEqual(body["profile"], ["android", "9.6.05", "9006005"])
        self.assertEqual(body["domain"], "https://interface3.music.163.com")
        self.assertEqual(body["timeout"], 12000)
        self.assertFalse(body["randomCNIP"])
        self.assertEqual(body["params"]["timbreKey"], "fixture-voice")
        ext = json.loads(body["params"]["extInfo"])
        self.assertTrue(ext["manualedTimbre"])
        self.assertNotIn("lbsInfoList", ext)
        self.assertEqual(response["credential"], credential)
        catalog = self.backend.request("netease", "personal_dj_voices", {}, credential)["data"]
        self.assertEqual(catalog["url"], "/api/aidj/audio/timbre/get")
        self.assertEqual(catalog["params"], {})
        with self.assertRaises(OnlineMusicError):
            self.backend.request("netease", "personal_dj", {"timbre_key": "../arbitrary"}, credential)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.node = shutil.which("node")
        if not self.node:
            self.skipTest("Node unavailable for actual-child transport checks")
        self.modules = self.root / "node_modules"
        manifest = self.modules / "@neteasecloudmusicapienhanced" / "api" / "package.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"version": "4.41.1"}), encoding="utf-8")
        self.backend = NodeOnlineMusicBackend(self.node, self.modules, timeout_seconds=1)
        self.backend.bridge_path = self.root / "fixture.cjs"
        self.addCleanup(self.close_fixture_backend)

    def close_fixture_backend(self):
        # Production close intentionally reaps off-thread for GUI shutdown.
        # Windows retains a child's cwd until termination; a test must wait
        # before TemporaryDirectory recursively removes that isolated cwd.
        processes = [self.backend._process]
        processes.extend(backend._process for backend in self.backend._authentication_backends.values())
        self.backend.close()
        for process in processes:
            if process is not None:
                process.wait(timeout=3)

    def authentication_backend(self, source="netease"):
        return self.backend._authentication_backends[source]

    def script(self, body):
        self.backend.bridge_path.write_text(body, encoding="utf-8")

    def qq_catalog_fixture(self):
        module = self.modules / "@yakult-green-tea" / "qq-music-api"
        service = module / "dist" / "src" / "services" / "auth" / "qrLogin.node.js"
        service.parent.mkdir(parents=True)
        (module / "package.json").write_text('{"version":"3.1.4"}', encoding="utf-8")
        service.write_text(r'''exports.createNodeQrLoginService=()=>({
          configureAuthSessionRepository:()=>{},
          getUserPlaylists:async()=>({v_playlist:[{tid:'playlist-1',diss_name:'fixture',song_cnt:7}]}),
          getOwnedPlaylistSongs:async(token,p)=>{
            if(p.disstid==='fallback')throw new Error('fixture fallback');
            return {songlist:[{mid:'mid-'+p.offset,name:'fixture'}],offset:p.offset,limit:p.limit};
          },
          getAuthenticatedSongListDetail:async(token,id,p)=>({songlist:[{mid:'fallback-mid',name:'fixture'}],limit:p.limit}),
          getMusicPlay:async(token,id)=>({[id]:{url:'https://example.org/'+id+'.m4a',error:''}})
        });''', encoding="utf-8")
        self.backend.bridge_path = Path(__file__).resolve().parents[1] / "native" / "online_music" / "bridge.cjs"
        return {"token": "synthetic-qq-token", "sessions": []}

    def write_modules(self, package, version, files):
        root = self.modules / package
        root.mkdir(parents=True, exist_ok=True)
        (root / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")
        for name, body in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        self.backend.bridge_path = Path(__file__).resolve().parents[1] / "native" / "online_music" / "bridge.cjs"

    def assert_invalid_confirmation(self, source, key):
        with self.assertRaises(OnlineMusicError) as raised:
            self.backend.request(source, "poll_login", {"key": key})
        self.assertEqual(raised.exception.code, "invalid_response")

    def test_dependency_absent_wrong_version_and_unsupported_are_explicit(self):
        self.script("")
        self.assertTrue(self.backend.available("netease")[0])
        self.assertFalse(self.backend.available("qq")[0])
        self.assertFalse(self.backend.available("qishui")[0])
        p = self.modules / "@neteasecloudmusicapienhanced" / "api" / "package.json"
        p.write_text('{"version":"9.9.9"}')
        self.assertFalse(self.backend.available("netease")[0])

    def test_actual_child_roundtrip_keeps_secret_out_of_argv_and_errors(self):
        self.script(r'''const rl=require('readline').createInterface({input:process.stdin});rl.on('line',line=>{const r=JSON.parse(line);process.stdout.write(JSON.stringify({id:r.id,ok:true,data:{operation:r.operation,hasCredential:!!r.credential},credential:r.credential})+'\n');});''')
        secret = {"cookie": {"MUSIC_U": "synthetic-secret-fixture"}}
        response = self.backend.request("netease", "account_status", {}, secret)
        self.assertEqual(response["credential"], secret)
        self.assertTrue(response["data"]["hasCredential"])
        self.assertNotIn("synthetic-secret", repr(self.authentication_backend()._process.args))

    def test_close_interrupts_pending_child_without_request_lock_wait(self):
        self.script("process.stdin.resume();setInterval(()=>{},1000);")
        self.backend.timeout_seconds = 18
        failures = []
        started = threading.Event()
        def request():
            started.set()
            try:
                self.backend.request("netease", "begin_login", {})
            except OnlineMusicError as error:
                failures.append(error.code)
        thread = threading.Thread(target=request)
        thread.start()
        self.assertTrue(started.wait(1))
        deadline = time.monotonic() + 2
        while ("netease" not in self.backend._authentication_backends
                or self.authentication_backend()._process is None) and time.monotonic() < deadline:
            time.sleep(.005)
        process = self.authentication_backend()._process
        before = time.monotonic()
        self.backend.close()
        self.assertLess(time.monotonic() - before, .15)
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, ["runtime"])
        process.wait(timeout=3)
        self.assertIsNotNone(process.poll())

    def test_untrusted_errors_are_sanitized(self):
        self.script(r'''const rl=require('readline').createInterface({input:process.stdin});rl.on('line',line=>{const r=JSON.parse(line);process.stdout.write(JSON.stringify({id:r.id,ok:false,error:'secret Cookie should never appear',message:'synthetic-secret-fixture'})+'\n');});''')
        with self.assertRaises(OnlineMusicError) as raised:
            self.backend.request("netease", "begin_login", {})
        self.assertEqual(raised.exception.code, "network")
        self.assertNotIn("secret", str(raised.exception))

    def test_timeout_terminates_abandoned_generation(self):
        self.script("process.stdin.resume();setInterval(()=>{},1000);")
        with self.assertRaises(OnlineMusicError) as raised:
            self.backend.request("netease", "begin_login", {})
        self.assertEqual(raised.exception.code, "timeout")
        self.assertIsNone(self.authentication_backend()._process)

    def test_catalog_network_wait_does_not_delay_login_or_stop_healthy_catalog(self):
        self.script(r'''const fs=require('fs');const rl=require('readline').createInterface({input:process.stdin});
          let sequence=Promise.resolve();rl.on('line',line=>{sequence=sequence.then(async()=>{
            const r=JSON.parse(line);if(r.operation==='lyrics'){
              fs.writeFileSync(r.params.marker,'started');await new Promise(resolve=>setTimeout(resolve,1200));
            }
            process.stdout.write(JSON.stringify({id:r.id,ok:true,data:{operation:r.operation}})+'\n');
          });});''')
        self.backend.timeout_seconds = 3
        marker, results = self.root / "catalog-started", []
        worker = threading.Thread(target=lambda: results.append(
            self.backend.request("netease", "lyrics", {"marker": str(marker)})))
        worker.start()
        self.addCleanup(worker.join, 3)
        deadline = time.monotonic() + 2
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(marker.exists())
        process = self.backend._process
        response = self.backend.request("netease", "begin_login", {})
        self.assertEqual(response["data"]["operation"], "begin_login")
        self.assertTrue(worker.is_alive(), "login waited for the unrelated catalog response")
        self.assertIs(self.backend._process, process)
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0]["data"]["operation"], "lyrics")
        self.assertEqual(self.backend.request("netease", "stream_url", {})["data"]["operation"], "stream_url")
        self.assertIs(self.backend._process, process)

    def test_other_platform_login_does_not_wait_for_abandoned_source_request(self):
        manifest = self.modules / "@yakult-green-tea/qq-music-api/package.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text('{"version":"3.1.4"}')
        self.script(r'''const fs=require('fs');const rl=require('readline').createInterface({input:process.stdin});
          let sequence=Promise.resolve();rl.on('line',line=>{sequence=sequence.then(async()=>{
            const r=JSON.parse(line);if(r.params.marker){
              fs.writeFileSync(r.params.marker,'started');await new Promise(resolve=>setTimeout(resolve,1200));
            }
            process.stdout.write(JSON.stringify({id:r.id,ok:true,data:{source:r.source}})+'\n');
          });});''')
        self.backend.timeout_seconds = 3
        marker, results = self.root / "login-started", []
        worker = threading.Thread(target=lambda: results.append(
            self.backend.request("netease", "begin_login", {"marker": str(marker)})))
        worker.start()
        self.addCleanup(worker.join, 3)
        deadline = time.monotonic() + 2
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(marker.exists())
        response = self.backend.request("qq", "begin_login", {})
        self.assertEqual(response["data"]["source"], "qq")
        self.assertTrue(worker.is_alive(), "source switching waited for the old QR request")
        worker.join(3)
        self.assertEqual(set(self.backend._authentication_backends), {"netease", "qq"})
        self.assertTrue(all(backend._authentication_lane for backend in self.backend._authentication_backends.values()))
        self.assertTrue(all(not backend._authentication_backends for backend in self.backend._authentication_backends.values()))
        self.assertIsNone(self.backend._process)

    def test_authentication_timeout_only_restarts_its_own_child(self):
        self.script(r'''const rl=require('readline').createInterface({input:process.stdin});
          rl.on('line',line=>{const r=JSON.parse(line);if(r.operation==='begin_login')return;
            process.stdout.write(JSON.stringify({id:r.id,ok:true,data:{}})+'\n');});''')
        self.backend.request("netease", "stream_url", {})
        process = self.backend._process
        with self.assertRaises(OnlineMusicError) as raised:
            self.backend.request("netease", "begin_login", {})
        self.assertEqual(raised.exception.code, "timeout")
        self.assertIsNone(self.authentication_backend()._process)
        self.assertIs(self.backend._process, process)
        self.backend.request("netease", "stream_url", {})
        self.assertIs(self.backend._process, process)

    def test_closed_backend_cannot_register_new_authentication_child(self):
        self.script("")
        self.backend.close()
        with self.assertRaises(OnlineMusicError) as raised:
            self.backend.request("netease", "begin_login", {})
        self.assertEqual(raised.exception.code, "closed")
        self.assertFalse(self.backend._authentication_backends)

    def test_close_interrupts_every_pending_transport_and_reaps_children(self):
        manifest = self.modules / "@yakult-green-tea/qq-music-api/package.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text('{"version":"3.1.4"}')
        self.script(r'''const fs=require('fs');const rl=require('readline').createInterface({input:process.stdin});
          rl.on('line',line=>{const r=JSON.parse(line);fs.writeFileSync(r.params.marker,'pending');});
          setInterval(()=>{},1000);''')
        self.backend.timeout_seconds = 18
        failures = []
        def request(source, operation, marker):
            try:
                self.backend.request(source, operation, {"marker": str(marker)})
            except OnlineMusicError as error:
                failures.append(error.code)
        requests = (("netease", "lyrics"), ("netease", "begin_login"), ("qq", "begin_login"))
        markers = [self.root / f"pending-{source}-{operation}" for source, operation in requests]
        workers = [threading.Thread(target=request, args=(*args, marker))
            for args, marker in zip(requests, markers)]
        for worker in workers:
            worker.start()
            self.addCleanup(worker.join, 3)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            processes = [self.backend._process]
            processes.extend(lane._process for lane in self.backend._authentication_backends.values())
            # A Popen assignment precedes stdin.write/flush. Wait until every
            # child has actually read its request before testing a pending wait.
            if (len(processes) == 3 and all(process is not None for process in processes)
                    and all(marker.exists() for marker in markers)):
                break
            time.sleep(.005)
        self.assertEqual(len(processes), 3)
        self.assertTrue(all(process is not None for process in processes))
        self.assertTrue(all(marker.exists() for marker in markers))
        allow_reaping = threading.Event()
        wait_entered = [threading.Event() for _ in processes]
        wait_finished = [threading.Event() for _ in processes]
        waiting_threads = [None] * len(processes)
        close_returned = threading.Event()
        close_errors = []

        def gated_wait(index, original_wait):
            def wait(*args, **kwargs):
                waiting_threads[index] = threading.current_thread()
                wait_entered[index].set()
                allow_reaping.wait()
                try:
                    return original_wait(*args, **kwargs)
                finally:
                    wait_finished[index].set()
            return wait

        def close():
            try:
                self.backend.close()
            except Exception as error:
                close_errors.append(error)
            finally:
                close_returned.set()

        # Prove causal ordering instead of a 150 ms wall-clock budget, which
        # also measures OS thread scheduling under concurrent packaging load.
        # Real child waits are deliberately held until close has returned and
        # all pending request threads have observed their runtime interruption.
        with ExitStack() as patches:
            for index, process in enumerate(processes):
                patches.enter_context(patch.object(process, "wait",
                    new=gated_wait(index, process.wait)))
            closer = threading.Thread(target=close, daemon=True)
            try:
                closer.start()
                self.assertTrue(close_returned.wait(3), "close waited for pending I/O or child reaping")
                self.assertFalse(close_errors)
                for entered in wait_entered:
                    self.assertTrue(entered.wait(3), "a child was not handed to its reaper")
                self.assertTrue(all(thread is not None and thread is not closer for thread in waiting_threads))
                self.assertFalse(any(finished.is_set() for finished in wait_finished))
                for worker in workers:
                    worker.join(2)
                    self.assertFalse(worker.is_alive())
                self.assertEqual(failures, ["runtime"] * 3)
            finally:
                allow_reaping.set()
                closer.join(3)
            self.assertFalse(closer.is_alive())
            self.assertTrue(all(finished.wait(3) for finished in wait_finished))
        for process in processes:
            process.wait(timeout=3)
            self.assertIsNotNone(process.poll())
        self.assertTrue(all(lane._closed for lane in self.backend._authentication_backends.values()))

    def test_preparation_only_loads_verified_modules_without_account_qr_or_network_calls(self):
        no_call = "module.exports=()=>{throw new Error('offline preparation called a network or QR function')};"
        self.write_modules("@neteasecloudmusicapienhanced/api", "4.41.1", {
            "util/request.js": no_call,
            "module/login_qr_key.js": no_call,
            "module/login_qr_create.js": no_call,
        })
        self.write_modules("kugoumusicapi", "1.6.0", {
            "util/index.js": "exports.calculateMid=()=>{throw new Error('device construction during preparation')};",
            "util/request.js": "exports.createRequest=()=>{throw new Error('network call during preparation')};",
            "module/register_dev.js": no_call,
            "module/login_qr_key.js": no_call,
            "module/login_qr_create.js": no_call,
        })
        self.write_modules("@yakult-green-tea/qq-music-api", "3.1.4", {
            "dist/src/services/auth/qrLogin.node.js": "exports.createNodeQrLoginService=()=>{throw new Error('account construction during preparation')};",
        })
        for source in ("netease", "kugou", "qq"):
            response = self.backend.request(source, "prepare_login", {}, {"fixture": "must-not-be-consumed"})
            self.assertEqual(response["data"], {"ready": True})
            self.assertIsNone(response["credential"])
        self.assertEqual(len(self.backend._authentication_backends), 3)
        self.assertIsNone(self.backend._process)

    def test_netease_preparation_initializes_cold_cache_before_request_module_import(self):
        no_call = "module.exports=()=>{throw new Error('offline preparation called a network or QR function')};"
        self.write_modules("@neteasecloudmusicapienhanced/api", "4.41.1", {
            "util/request.js": r'''const fs=require('fs'),os=require('os'),path=require('path');
              if(fs.readFileSync(path.join(os.tmpdir(),'anonymous_token'),'utf8')!=='')
                throw new Error('cold cache was not initialized before import');
              module.exports=()=>{throw new Error('network function called during preparation')};''',
            "module/login_qr_key.js": no_call,
            "module/login_qr_create.js": no_call,
        })
        token = self.root / "GluSpot" / "cache" / "online-music-bridge" / "anonymous_token"
        self.assertFalse(token.exists())
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}):
            response = self.backend.request("netease", "prepare_login", {},
                {"cookie": {"MUSIC_U": "synthetic-must-not-be-consumed"}})
        self.assertEqual(response["data"], {"ready": True})
        self.assertIsNone(response["credential"])
        self.assertEqual(token.read_bytes(), b"")
        self.assertIsNone(self.backend._process)

    def test_netease_preparation_preserves_existing_anonymous_token_and_mtime(self):
        no_call = "module.exports=()=>{throw new Error('offline preparation called a network or QR function')};"
        self.write_modules("@neteasecloudmusicapienhanced/api", "4.41.1", {
            "util/request.js": r'''const fs=require('fs'),os=require('os'),path=require('path');
              if(fs.readFileSync(path.join(os.tmpdir(),'anonymous_token'),'utf8')!=='synthetic-anonymous-fixture')
                throw new Error('existing anonymous token was replaced before import');
              module.exports=()=>{throw new Error('network function called during preparation')};''',
            "module/login_qr_key.js": no_call,
            "module/login_qr_create.js": no_call,
        })
        token = self.root / "GluSpot" / "cache" / "online-music-bridge" / "anonymous_token"
        token.parent.mkdir(parents=True)
        token.write_bytes(b"synthetic-anonymous-fixture")
        os.utime(token, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
        mtime_ns = token.stat().st_mtime_ns
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}):
            response = self.backend.request("netease", "prepare_login", {},
                {"cookie": {"MUSIC_U": "synthetic-must-not-be-consumed"}})
        self.assertEqual(response["data"], {"ready": True})
        self.assertIsNone(response["credential"])
        self.assertEqual(token.read_bytes(), b"synthetic-anonymous-fixture")
        self.assertEqual(token.stat().st_mtime_ns, mtime_ns)
        self.assertIsNone(self.backend._process)

    def test_constructor_and_availability_do_not_start_login_children(self):
        self.script("")
        self.assertTrue(self.backend.available("netease")[0])
        self.assertFalse(self.backend._authentication_backends)
        self.assertIsNone(self.backend._process)

    def test_prepare_is_async_idempotent_and_keeps_login_on_prepared_lane(self):
        marker = self.root / "prepared"
        self.script(r'''const fs=require('fs');let prepared=0;
          const rl=require('readline').createInterface({input:process.stdin});rl.on('line',line=>{
            const r=JSON.parse(line);if(r.operation==='prepare_login'){
              prepared++;fs.writeFileSync(require('path').join(__dirname,'prepared'),'done');
            }
            process.stdout.write(JSON.stringify({id:r.id,ok:true,data:{prepared}})+'\n');
          });''')
        self.assertTrue(self.backend.prepare_login("netease"))
        deadline = time.monotonic() + 2
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(marker.exists())
        process = self.authentication_backend()._process
        self.assertTrue(self.backend.prepare_login("netease"))
        self.assertEqual(self.backend.request("netease", "begin_login", {})["data"]["prepared"], 1)
        self.assertIs(self.authentication_backend()._process, process)
        self.assertFalse(self.backend.prepare_login("qishui"))
        self.backend.close()
        process.wait(timeout=3)
        self.assertFalse(self.backend.prepare_login("netease"))

    def test_unused_preparation_expires_on_daemon_timer_without_starting_catalog(self):
        self.script("")
        self.backend._prepared_idle_seconds = .025
        lane = self.backend._authentication_backend("netease")
        with patch.object(lane, "request", return_value={"data": {"ready": True}}):
            self.assertTrue(self.backend.prepare_login("netease"))
            timer = lane._prepare_retirement_timer
            self.assertTrue(timer.daemon)
            deadline = time.monotonic() + 1
            while ("netease" in self.backend._authentication_backends or not lane._closed) and time.monotonic() < deadline:
                time.sleep(.005)
        self.assertNotIn("netease", self.backend._authentication_backends)
        self.assertTrue(lane._closed)
        self.assertIsNone(self.backend._process)

    def test_real_authentication_claims_lane_before_waiting_for_its_lock(self):
        self.script(r'''const rl=require('readline').createInterface({input:process.stdin});
          rl.on('line',line=>{const r=JSON.parse(line);process.stdout.write(JSON.stringify({id:r.id,ok:true,data:{}})+'\n');});''')
        lane = self.backend._authentication_backend("netease")
        results = []
        with lane._lock:
            worker = threading.Thread(target=lambda: results.append(
                self.backend.request("netease", "begin_login", {})))
            worker.start()
            self.addCleanup(worker.join, 3)
            deadline = time.monotonic() + 1
            while not lane._authentication_used and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertTrue(lane._authentication_used)
            self.backend._retire_prepared_authentication_backend("netease", lane)
            self.assertIs(self.authentication_backend(), lane)
            self.assertFalse(lane._closed)
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(results), 1)

    def test_old_retirement_callback_cannot_close_a_newly_registered_lane(self):
        self.script("")
        old = self.backend._authentication_backend("netease")
        self.backend._retire_prepared_authentication_backend("netease", old)
        self.assertTrue(old._closed)
        new = self.backend._authentication_backend("netease", used=True)
        self.backend._retire_prepared_authentication_backend("netease", old)
        self.assertIs(self.authentication_backend(), new)
        self.assertFalse(new._closed)

    def test_retirement_reaps_a_real_prepared_only_child(self):
        self.script(r'''const rl=require('readline').createInterface({input:process.stdin});
          rl.on('line',line=>{const r=JSON.parse(line);process.stdout.write(JSON.stringify({id:r.id,ok:true,data:{ready:true}})+'\n');});''')
        self.assertTrue(self.backend.prepare_login("netease"))
        self.backend.request("netease", "prepare_login", {})
        lane, process = self.authentication_backend(), self.authentication_backend()._process
        self.assertFalse(lane._authentication_used)
        retire = threading.Thread(target=self.backend._retire_prepared_authentication_backend,
            args=("netease", lane), daemon=True)
        retire.start()
        retire.join(1)
        self.assertFalse(retire.is_alive())
        self.assertNotIn("netease", self.backend._authentication_backends)
        self.assertTrue(lane._closed)
        process.wait(timeout=3)
        self.assertIsNotNone(process.poll())
        self.assertIsNone(self.backend._process)

    def test_close_cancels_preparation_timer_and_closed_callback_is_harmless(self):
        self.script("")
        lane = self.backend._authentication_backend("netease")
        with patch.object(lane, "request", return_value={"data": {"ready": True}}):
            self.assertTrue(self.backend.prepare_login("netease"))
        timer = lane._prepare_retirement_timer
        self.backend.close()
        self.assertTrue(timer.finished.is_set())
        self.assertTrue(lane._closed)
        self.backend._retire_prepared_authentication_backend("netease", lane)
        self.assertFalse(self.backend.prepare_login("netease"))
        self.assertIs(self.authentication_backend(), lane)

    def test_inherited_account_material_is_removed(self):
        self.script(r'''const rl=require('readline').createInterface({input:process.stdin});rl.on('line',line=>{const r=JSON.parse(line);process.stdout.write(JSON.stringify({id:r.id,ok:true,data:{unsafe:Object.keys(process.env).some(k=>/COOKIE|TOKEN|SECRET/i.test(k))}})+'\n');});''')
        with patch.dict(os.environ, {"NETEASE_COOKIE": "synthetic", "OTHER_TOKEN": "synthetic"}):
            response = self.backend.request("netease", "begin_login", {})
        self.assertFalse(response["data"]["unsafe"])

    def test_qq_actual_bridge_preserves_catalog_and_normalizes_stream_map(self):
        credential = self.qq_catalog_fixture()
        playlists = self.backend.request("qq", "playlists", {}, credential)["data"]
        self.assertEqual(playlists["v_playlist"][0]["tid"], "playlist-1")
        tracks = self.backend.request("qq", "playlist_tracks", {"playlist_id": "playlist-1", "offset": 200, "limit": 1000}, credential)["data"]
        self.assertTrue(tracks["_windowed"])
        self.assertEqual(tracks["songlist"][0]["mid"], "mid-200")
        self.assertEqual(tracks["limit"], 100)
        stream = self.backend.request("qq", "stream_url", {"song_id": "track-mid"}, credential)["data"]
        self.assertEqual(stream["url"], "https://example.org/track-mid.m4a")
        self.assertNotIn("track-mid", stream)

    def test_qq_fallback_is_bounded_and_does_not_claim_windowed_data(self):
        credential = self.qq_catalog_fixture()
        fallback = self.backend.request("qq", "playlist_tracks", {"playlist_id": "fallback", "offset": 995, "limit": 100}, credential)["data"]
        self.assertNotIn("_windowed", fallback)
        self.assertEqual(fallback["limit"], 1000)
        with self.assertRaises(OnlineMusicError) as raised:
            self.backend.request("qq", "playlist_tracks", {"playlist_id": "fallback", "offset": 1000, "limit": 100}, credential)
        self.assertEqual(raised.exception.code, "unsupported")

    def test_netease_fresh_qr_isolated_from_old_account_and_requires_new_cookie(self):
        self.write_modules("@neteasecloudmusicapienhanced/api", "4.41.1", {
            "module/login_qr_key.js": "let i=0;module.exports=async()=>({body:{data:{unikey:'qr-'+(++i)}}});",
            "module/login_qr_create.js": "module.exports=async()=>({body:{data:{qrimg:'fixture'}}});",
            "module/login_status.js": "module.exports=async q=>({body:{profile:{userId:q.cookie.MUSIC_U==='new-cookie'?2:1}}});",
            "util/request.js": r'''module.exports=async(url,data)=>({body:{code:803},cookie:data.key==='qr-2'?['MUSIC_U=new-cookie']:[]});''',
        })
        old = {"cookie": {"MUSIC_U": "old-cookie"}}
        self.backend.request("netease", "account_status", {}, old)
        first = self.backend.request("netease", "begin_login", {}, old)
        self.assertNotIn("MUSIC_U", first["credential"]["cookie"])
        # A queued old account read must not import its credential into the QR.
        self.backend.request("netease", "account_status", {}, old)
        self.assert_invalid_confirmation("netease", first["data"]["key"])
        second = self.backend.request("netease", "begin_login", {})
        self.backend.request("netease", "account_status", {}, old)
        confirmed = self.backend.request("netease", "poll_login", {"key": second["data"]["key"]})
        self.assertEqual(confirmed["data"]["state"], "confirmed")
        self.assertEqual(confirmed["credential"]["cookie"]["MUSIC_U"], "new-cookie")
        account = self.backend.request("netease", "account_status", {}, confirmed["credential"])
        self.assertEqual(account["data"]["user_id"], "2")

    def test_kugou_fresh_qr_rejects_missing_and_undefined_session(self):
        self.write_modules("kugoumusicapi", "1.6.0", {
            "util/index.js": "exports.calculateMid=()=>1;",
            "util/request.js": "exports.createRequest=()=>{};",
            "module/register_dev.js": "module.exports=async()=>({body:{status:1},cookie:['dfid=fixture-device']});",
            "module/login_qr_key.js": "let i=0;module.exports=async()=>({body:{data:{qrcode:'kg-'+(++i)}}});",
            "module/login_qr_create.js": "module.exports=async()=>({body:{data:{base64:'fixture'}}});",
            "module/login_qr_check.js": r'''module.exports=async p=>{
              const data=p.key==='kg-1'?{status:4}:p.key==='kg-2'?{status:4,token:'undefined',userid:'null'}:{status:4,token:'new-token',userid:22};
              return {body:{data},cookie:['token='+data.token,'userid='+data.userid]};
            };''',
            "module/user_detail.js": "module.exports=async()=>({body:{status:1,error_code:0,data:{nickname:'fixture'}}});",
        })
        old = {"cookie": {"token": "old-token", "userid": "11"}}
        self.backend.request("kugou", "account_status", {}, old)
        for _ in range(2):
            attempt = self.backend.request("kugou", "begin_login", {}, old)
            self.assertNotIn("token", attempt["credential"]["cookie"])
            self.assertNotIn("userid", attempt["credential"]["cookie"])
            self.backend.request("kugou", "account_status", {}, old)
            self.assert_invalid_confirmation("kugou", attempt["data"]["key"])
        attempt = self.backend.request("kugou", "begin_login", {})
        self.backend.request("kugou", "account_status", {}, old)
        confirmed = self.backend.request("kugou", "poll_login", {"key": attempt["data"]["key"]})
        self.assertEqual(confirmed["credential"]["cookie"]["token"], "new-token")
        self.assertEqual(confirmed["credential"]["cookie"]["userid"], "22")

    def test_qq_new_service_keeps_old_repository_out_of_qr_confirmation(self):
        self.write_modules("@yakult-green-tea/qq-music-api", "3.1.4", {
            "dist/src/services/auth/qrLogin.node.js": r'''let count=0;exports.createNodeQrLoginService=()=>{
              const instance=++count;let repo,sessions=[];
              return {
                configureAuthSessionRepository:r=>{repo=r;sessions=[...r.load(),...sessions];r.save(sessions);},
                createSession:async()=>('qq-'+instance), createQr:async()=>('fixture'),
                checkQr:async()=>{
                  if(instance===2)return {code:803};
                  const token='new-token';sessions.push({token,credential:{synthetic:true}});repo.save(sessions);
                  return {code:803,cookie:'qqmusic_session='+token};
                },
                getLoginStatus:async token=>({userId:token==='new-token'?22:11}),
                logout:async()=>{}
              };
            };''',
        })
        old = {"token": "old-token", "sessions": [{"token": "old-token", "credential": {"synthetic": True}}]}
        self.backend.request("qq", "account_status", {}, old)
        first = self.backend.request("qq", "begin_login", {}, old)
        self.assertEqual(first["credential"], {"token": "", "sessions": []})
        self.backend.request("qq", "account_status", {}, old)
        self.assert_invalid_confirmation("qq", first["data"]["key"])
        second = self.backend.request("qq", "begin_login", {})
        self.assertEqual(second["credential"]["sessions"], [])
        self.backend.request("qq", "account_status", {}, old)
        confirmed = self.backend.request("qq", "poll_login", {"key": second["data"]["key"]})
        self.assertEqual(confirmed["credential"]["token"], "new-token")
        self.assertEqual([s["token"] for s in confirmed["credential"]["sessions"]], ["new-token"])

    def test_netease_search_uses_native_offset_and_distinct_search_types(self):
        self.write_modules("@neteasecloudmusicapienhanced/api", "4.41.1", {
            "util/request.js": "module.exports=()=>{};",
            "module/cloudsearch.js": r'''module.exports=async p=>({body:{code:200,result:p.type===1?
              {songs:[{id:p.offset+1,name:p.keywords,dt:1000,limit:p.limit}],songCount:7}:
              {playlists:[{id:p.offset+2,name:p.keywords,trackCount:2}],playlistCount:4}}});''',
        })
        credential = {"cookie": {"MUSIC_U": "synthetic-cookie"}}
        songs = self.backend.request("netease", "search", {"query": "keyword", "kind": "songs", "limit": 2, "offset": 2}, credential)["data"]
        self.assertEqual(songs["items"][0]["id"], 3)
        self.assertEqual(songs["items"][0]["limit"], 2)
        self.assertEqual((songs["total"], songs["next_offset"], songs["has_more"]), (7, 3, True))
        playlists = self.backend.request("netease", "search", {"query": "keyword", "kind": "playlists", "limit": 2, "offset": 3}, credential)["data"]
        self.assertEqual(playlists["items"][0]["id"], 5)
        self.assertEqual((playlists["next_offset"], playlists["has_more"]), (4, False))

    def test_netease_stream_uses_pinned_standard_module_without_xeapi_startup(self):
        self.write_modules("@neteasecloudmusicapienhanced/api", "4.41.1", {
            "main.js": "throw new Error('upstream server entry must not load');",
            "util/xeapiKey.js": "throw new Error('xeapi startup must not load');",
            "module/song_url_v1.js": "throw new Error('unblock imports must not load');",
            "module/song_url.js": r'''module.exports=async(query,request)=>{
              if(query.br!==128000 || query.timeout!==12000 || query.randomCNIP!==false ||
                 query.cookie.MUSIC_U!=='synthetic-cookie' || query.unblock!==undefined)
                throw new Error('wrong standard module contract');
              return request('/api/song/enhance/player/url',{
                ids:JSON.stringify(String(query.id).split(',')),br:query.br
              },{cookie:query.cookie,timeout:query.timeout});
            };''',
            "util/request.js": r'''module.exports=async(url,data,options)=>{
              if(url!=='/api/song/enhance/player/url' || data.br!==128000 ||
                 options.cookie.MUSIC_U!=='synthetic-cookie' || options.timeout!==12000)
                throw new Error('wrong stream request contract');
              return {body:{code:200,data:[{id:Number(JSON.parse(data.ids)[0]),
                url:'https://example.org/fixture.mp3',code:200,fee:0,freeTrialInfo:null,br:data.br}]}};
            };''',
        })
        credential = {"cookie": {"MUSIC_U": "synthetic-cookie"}}
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}):
            for song in ("1", "2"):
                stream = self.backend.request("netease", "stream_url", {"song_id": song}, credential)["data"]
                entry = stream["data"][0]
                self.assertEqual(entry["id"], int(song))
                self.assertEqual(entry["br"], 128000)
                self.assertIsNone(entry["freeTrialInfo"])
            self.assertFalse((self.root / "GluSpot/cache/online-music-bridge/xeapi_public_key").exists())

    def test_netease_standard_stream_preserves_trial_and_unavailable_results(self):
        self.write_modules("@neteasecloudmusicapienhanced/api", "4.41.1", {
            "util/request.js": "module.exports=()=>{};",
            "module/song_url.js": r'''module.exports=async query=>({body:{code:200,data:[
              query.id==='trial'?{id:1,url:'https://example.org/trial.mp3',code:200,fee:1,freeTrialInfo:{start:1000,end:31000}}:
              {id:2,url:null,code:-110,fee:1,freeTrialInfo:null,message:'fixture unavailable'}
            ]}});''',
        })
        credential = {"cookie": {"MUSIC_U": "synthetic-cookie"}}
        trial = self.backend.request("netease", "stream_url", {"song_id": "trial"}, credential)["data"]["data"][0]
        self.assertEqual(trial["freeTrialInfo"], {"start": 1000, "end": 31000})
        self.assertEqual((trial["code"], trial["fee"]), (200, 1))
        blocked = self.backend.request("netease", "stream_url", {"song_id": "blocked"}, credential)["data"]["data"][0]
        self.assertIsNone(blocked["url"])
        self.assertEqual((blocked["code"], blocked["fee"]), (-110, 1))

    def test_qq_search_uses_verified_public_contract_and_sparse_page_metadata(self):
        credential = self.qq_catalog_fixture()
        client = self.modules / "@yakult-green-tea" / "qq-music-api" / "dist" / "src" / "services" / "auth" / "httpClient.js"
        client.write_text(r'''exports.default=()=>({post:async(url,p,config)=>{
          const q=p.request.param,field=q.search_type===0?'song':'songlist';
          if(url!=='https://u.y.qq.com/cgi-bin/musicu.fcg'||p.request.module!=='music.search.SearchCgiService'||p.request.method!=='DoSearchForQQMusicDesktop'||config.headers.Cookie!=='tmeLoginType=-1;')throw new Error('wrong contract');
          const rows=Array.from({length:q.num_per_page},(_,i)=>({mid:'mid-'+i,dissid:'list-'+i,name:q.query,page:q.page_num,limit:q.num_per_page}));
          return {data:{code:0,request:{code:0,data:{body:{[field]:{list:rows}},meta:{sum:99,nextpage:q.page_num+1,is_filter:q.query==='filtered'?-2:0}}}}};
        }});''', encoding="utf-8")
        songs = self.backend.request("qq", "search", {"query": "keyword", "kind": "songs", "limit": 3, "offset": 3}, credential)["data"]
        self.assertEqual(songs["items"][0]["page"], 2)
        self.assertEqual((songs["total"], songs["next_offset"], songs["has_more"]), (99, 6, True))
        lists = self.backend.request("qq", "search", {"query": "keyword", "kind": "playlists", "limit": 3, "offset": 4}, credential)["data"]
        self.assertEqual([item["dissid"] for item in lists["items"]], ["list-1", "list-2"])
        self.assertEqual(lists["next_offset"], 6)
        with self.assertRaises(OnlineMusicError) as raised:
            self.backend.request("qq", "search", {"query": "filtered", "kind": "songs", "limit": 3, "offset": 0}, credential)
        self.assertEqual(raised.exception.code, "invalid_response")

    def test_kugou_search_preserves_page_and_global_playlist_namespace(self):
        self.write_modules("kugoumusicapi", "1.6.0", {
            "util/index.js": "exports.calculateMid=()=>1;",
            "util/request.js": "exports.createRequest=()=>{};",
            "module/search.js": r'''module.exports=async p=>({body:{status:1,error_code:0,data:{total:12,lists:
              Array.from({length:p.pagesize},(_,i)=>p.type==='song'?{FileHash:'hash-'+i,SongName:p.keywords,AlbumName:'album',page:p.page}:
              {gid:'collection_3_42_'+i+'_0',specialid:i,specialname:p.keywords,page:p.page})}}});''',
        })
        credential = {"cookie": {"token": "synthetic-token", "userid": "42"}}
        songs = self.backend.request("kugou", "search", {"query": "keyword", "kind": "songs", "limit": 3, "offset": 4}, credential)["data"]
        self.assertEqual(songs["items"][0]["FileHash"], "hash-1")
        self.assertEqual(songs["items"][0]["page"], 2)
        self.assertEqual((songs["next_offset"], songs["total"], songs["has_more"]), (6, 12, True))
        lists = self.backend.request("kugou", "search", {"query": "keyword", "kind": "playlists", "limit": 3, "offset": 9}, credential)["data"]
        self.assertEqual(lists["items"][0]["gid"], "collection_3_42_0_0")
        self.assertEqual((lists["next_offset"], lists["has_more"]), (12, False))

    def test_kugou_search_rejects_failed_status_even_with_rows(self):
        self.write_modules("kugoumusicapi", "1.6.0", {
            "util/index.js": "exports.calculateMid=()=>1;",
            "util/request.js": "exports.createRequest=()=>{};",
            "module/search.js": r'''module.exports=async p=>{
              const data={total:1,lists:[{FileHash:'fixture-hash',SongName:'fixture'}]};
              const bodies={
                failed_status:{status:0,error_code:0,data},
                failed_string_status:{status:'0',data},
                auth_required:{status:1,error_code:152,data:{}},
                error_with_rows:{status:1,error_code:'152',data},
                missing_status:{data},
                array_data:{status:1,data:[]},
                valid_strings:{status:'1',error_code:'0',data},
                valid_without_error_code:{status:1,data}
              };
              return {body:bodies[p.keywords]};
            };''',
        })
        credential = {"cookie": {"token": "synthetic-token", "userid": "42"}}
        for query in ("failed_status", "failed_string_status", "auth_required", "error_with_rows", "missing_status", "array_data"):
            with self.subTest(query=query):
                with self.assertRaises(OnlineMusicError) as raised:
                    self.backend.request("kugou", "search", {"query": query, "kind": "songs", "limit": 3, "offset": 0}, credential)
                self.assertEqual(raised.exception.code, "invalid_response")
        for query in ("valid_strings", "valid_without_error_code"):
            with self.subTest(query=query):
                page = self.backend.request("kugou", "search", {"query": query, "kind": "songs", "limit": 3, "offset": 0}, credential)["data"]
                self.assertEqual(page["items"][0]["FileHash"], "fixture-hash")
                self.assertEqual((page["total"], page["next_offset"], page["has_more"]), (1, 1, False))


if __name__ == "__main__":
    unittest.main()
