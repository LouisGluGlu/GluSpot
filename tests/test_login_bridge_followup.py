"""Actual child cancellation/deadline checks and pinned SDK device injection."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from api.online_music import OnlineMusicError, OnlineMusicService
from api.online_music_bridge import NodeOnlineMusicBackend
from api.online_music_credentials import ProtectedCredentialStore
from api.online_music_diagnostics import current_login_trace, login_trace, login_request_guard
from tests.test_online_music import FixtureProtector, PNG


class LoginBridgeFollowupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.node = shutil.which("node")
        if not self.node:
            self.skipTest("Node unavailable")
        self.modules = self.root / "node_modules"
        for name, version in (("@neteasecloudmusicapienhanced/api", "4.41.1"), ("@yakult-green-tea/qq-music-api", "3.1.4")):
            package = self.modules / name
            package.mkdir(parents=True)
            (package / "package.json").write_text(json.dumps({"version": version}))
        self.backend = NodeOnlineMusicBackend(self.node, self.modules, timeout_seconds=2)
        self.backend.bridge_path = self.root / "fixture.cjs"
        self.backend.bridge_path.write_text(r'''const fs=require('fs'),path=require('path');
          require('readline').createInterface({input:process.stdin}).on('line',line=>{
            const r=JSON.parse(line);fs.writeFileSync(path.join(__dirname,r.source+'-'+r.operation),'');
            if(r.params.slow)return;
            process.stdout.write(JSON.stringify({id:r.id,ok:true,data:{budget:r.timeout_ms}})+'\n');
          });''', encoding="utf-8")
        self.addCleanup(self.close)

    def close(self):
        lanes=[self.backend,*self.backend._authentication_backends.values()]
        children=[p for lane in lanes for p in [lane._process,*lane._retiring_processes] if p]
        self.backend.close()
        for p in children:
            p.wait(timeout=3)

    def wait_marker(self, source="netease", operation="begin_login"):
        end=time.monotonic()+2
        while not (self.root/(source+"-"+operation)).exists() and time.monotonic()<end:
            time.sleep(.005)
        self.assertTrue((self.root/(source+"-"+operation)).exists())

    def slow(self, source="netease", operation="begin_login"):
        errors=[]
        def run():
            try:self.backend.request(source,operation,{"slow":True})
            except OnlineMusicError as exc:errors.append(exc.code)
        worker=threading.Thread(target=run)
        worker.start();self.addCleanup(worker.join,3)
        self.wait_marker(source,operation)
        return worker,errors

    def test_cancel_wakes_owned_request_and_replacement_does_not_wait_for_old_timeout(self):
        worker,errors=self.slow()
        process=self.backend._authentication_backends["netease"]._process
        self.assertTrue(self.backend.cancel_login_request("netease"))
        worker.join(.8)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors,["cancelled"])
        response=self.backend.request("netease","begin_login",{})
        self.assertGreater(response["data"]["budget"],0)
        self.assertIsNot(self.backend._authentication_backends["netease"]._process,process)
        process.wait(timeout=2)

    def test_cancel_preserves_idle_qr_other_platform_and_catalog(self):
        self.backend.request("qq","begin_login",{})
        idle=self.backend._authentication_backends["qq"]._process
        self.assertFalse(self.backend.cancel_login_request("qq"))
        worker,errors=self.slow("netease")
        catalog,catalog_errors=self.slow("netease","lyrics")
        self.assertTrue(self.backend.cancel_login_request("netease"))
        worker.join(1)
        self.assertEqual(errors,["cancelled"])
        self.assertTrue(catalog.is_alive())
        self.assertIsNone(idle.poll())
        self.backend.close();catalog.join(1)
        self.assertEqual(catalog_errors,["runtime"])

    def test_auth_lock_wait_consumes_trace_budget_without_spawning_child(self):
        lane=self.backend._authentication_backend("netease")
        errors=[]
        def run():
            with login_trace("netease","begin",9,timeout_seconds=.09):
                try:self.backend.request("netease","begin_login",{})
                except OnlineMusicError as exc:errors.append((exc.code,exc.stage))
        with lane._lock:
            worker=threading.Thread(target=run);worker.start();worker.join(.7)
            self.assertFalse(worker.is_alive())
        self.assertEqual(errors,[("timeout","auth_queue")])
        self.assertIsNone(lane._process)

    def test_cancel_discards_same_source_task_already_waiting_for_auth_lock(self):
        lane=self.backend._authentication_backend("netease")
        errors=[];started=threading.Event()
        original=lane._lock
        class ObservedLock:
            def acquire(self,**kwargs):started.set();return original.acquire(**kwargs)
            def release(self):original.release()
        lane._lock=ObservedLock()
        def run():
            try:self.backend.request("netease","begin_login",{})
            except OnlineMusicError as exc:errors.append(exc.code)
        with original:
            worker=threading.Thread(target=run);worker.start()
            self.assertTrue(started.wait(1))
            self.assertFalse(self.backend.cancel_login_request("netease"))
        worker.join(1)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,["cancelled"])
        self.assertIsNone(lane._process)

    def test_cancelled_service_ownership_cannot_register_a_late_auth_child(self):
        cancelled=threading.Event()
        cancelled.set()
        with login_request_guard(cancelled):
            with self.assertRaises(OnlineMusicError) as raised:
                self.backend.request("qq","begin_login",{})
            self.assertEqual(raised.exception.code,"cancelled")
            self.assertFalse(self.backend._authentication_backends)
            # Catalog calls do not inherit authentication-only cancellation.
            self.assertGreater(self.backend.request("qq","lyrics",{})["data"]["budget"],0)

    def test_service_ownership_is_checked_again_after_auth_queue(self):
        lane=self.backend._authentication_backend("netease")
        cancelled=threading.Event();entered=threading.Event();errors=[]
        original=lane._lock
        class ObservedLock:
            def acquire(self,**kwargs):entered.set();return original.acquire(**kwargs)
            def release(self):original.release()
        lane._lock=ObservedLock()
        def run():
            with login_request_guard(cancelled):
                try:self.backend.request("netease","begin_login",{})
                except OnlineMusicError as exc:errors.append(exc.code)
        with original:
            worker=threading.Thread(target=run);worker.start()
            self.assertTrue(entered.wait(1));cancelled.set()
        worker.join(1)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,["cancelled"])
        self.assertIsNone(lane._process)

    def test_qq_cancel_during_local_record_read_never_sends_retired_begin(self):
        # QQ reads its protected device context between generation registration
        # and transport entry. Refreshing there must not leave a stale request
        # ahead of the replacement, even when no old child existed to terminate.
        self.backend.bridge_path.write_text("const fs=require('fs'),path=require('path');"
            "require('readline').createInterface({input:process.stdin}).on('line',line=>{"
            "const r=JSON.parse(line);fs.appendFileSync(path.join(__dirname,'requests'),r.operation+'\\n');"
            "process.stdout.write(JSON.stringify({id:r.id,ok:true,data:{key:'offline-fixture',qr_image:"
            +json.dumps(PNG)+",ttl:180}})+'\\n');});",encoding="utf-8")
        store=ProtectedCredentialStore(self.root/"credentials",FixtureProtector())
        service=OnlineMusicService(self.backend,store)
        self.addCleanup(service.close)
        entered=threading.Event();release=threading.Event();results=[];errors=[]
        def load(source):
            if threading.current_thread().name=="retired-login":
                entered.set()
                if not release.wait(2):raise AssertionError("credential fixture timed out")
            return None
        def retired():
            try:results.append(service.begin_login("qq"))
            except OnlineMusicError as exc:errors.append(exc.code)
        with patch.object(store,"load",side_effect=load):
            worker=threading.Thread(target=retired,name="retired-login");worker.start()
            try:
                self.assertTrue(entered.wait(1))
                service.invalidate_source_login("qq")
                replacement=service.begin_login("qq")
                self.assertEqual(replacement.state,"waiting")
            finally:
                release.set();worker.join(1)
        self.assertFalse(worker.is_alive());self.assertFalse(results)
        self.assertEqual(errors,["cancelled"])
        self.assertEqual((self.root/"requests").read_text().splitlines(),["begin_login"])

    def test_remaining_node_budget_includes_waiting_for_auth_lock(self):
        lane=self.backend._authentication_backend("netease")
        results=[]
        # Start the child first so this assertion measures queue budget rather
        # than operating-system process startup scheduling.
        self.backend.request("netease","begin_login",{})
        with lane._lock:
            def run():
                with login_trace("netease","begin",1,timeout_seconds=.8):
                    results.append(self.backend.request("netease","begin_login",{}))
            worker=threading.Thread(target=run);worker.start();time.sleep(.15)
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertLess(results[0]["data"]["budget"],700)
        self.assertGreater(results[0]["data"]["budget"],0)

    def test_failed_preparation_can_retry_without_adding_a_second_lane(self):
        lane=self.backend._authentication_backend("netease")
        with patch.object(lane,"request",side_effect=[OnlineMusicError("network"),{"data":{"ready":True}}]) as request:
            self.assertTrue(self.backend.prepare_login("netease"))
            deadline=time.monotonic()+1
            while lane._prepare_started and time.monotonic()<deadline:time.sleep(.005)
            self.assertFalse(lane._prepare_started)
            self.assertTrue(self.backend.prepare_login("netease"))
            deadline=time.monotonic()+1
            while request.call_count<2 and time.monotonic()<deadline:time.sleep(.005)
            self.assertEqual(request.call_count,2)
        self.assertEqual(len(self.backend._authentication_backends),1)

    def test_async_preparation_keeps_its_safe_generation_and_total_deadline(self):
        lane=self.backend._authentication_backend("netease")
        entered=threading.Event();release=threading.Event();done=threading.Event();seen=[]
        def prepare(*args):
            entered.set();release.wait(2);seen.append(current_login_trace());done.set()
            return {"data":{"ready":True}}
        with patch.object(lane,"request",side_effect=prepare):
            with login_trace("netease","prepare",17) as original:
                self.assertTrue(self.backend.prepare_login("netease"))
                self.assertTrue(entered.wait(1))
            release.set();self.assertTrue(done.wait(1))
        self.assertIs(seen[0],original)

    def test_child_that_never_reads_a_large_auth_envelope_still_times_out(self):
        self.backend.bridge_path.write_text("setInterval(()=>{},1000);",encoding="utf-8")
        self.backend.timeout_seconds=1
        errors=[]
        def run():
            try:self.backend.request("netease","account_status",{},{"synthetic":"x"*(3*1024*1024)})
            except OnlineMusicError as exc:errors.append(exc.code)
        worker=threading.Thread(target=run);worker.start();self.addCleanup(worker.join,3)
        worker.join(2)
        self.assertFalse(worker.is_alive(),"stdin write exceeded the login request budget")
        self.assertEqual(errors,["timeout"])

    def test_new_child_is_refused_while_two_retirements_are_not_reaped(self):
        lane=self.backend._authentication_backend("netease")
        sentinel1,sentinel2=object(),object()
        with lane._lifecycle_lock:lane._retiring_processes.update((sentinel1,sentinel2))
        try:
            with self.assertRaises(OnlineMusicError) as raised:self.backend.request("netease","begin_login",{})
            self.assertEqual(raised.exception.code,"runtime")
            self.assertIsNone(lane._process)
        finally:lane._retiring_processes.clear()


class PinnedQqDeviceContextTests(unittest.TestCase):
    def test_real_sdk_terminal_poll_diagnostics_have_safe_distinct_bridge_results(self):
        root=Path(__file__).resolve().parents[1]
        node=root/"build/online-music/node.exe"
        module=root/"build/online-music/node_modules/@yakult-green-tea/qq-music-api"
        if not node.is_file() or not module.is_dir():self.skipTest("Pinned SDK unavailable")
        script=r'''
          const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
          process.env.NODE_ENV='test';
          for(const name of ['log','warn','error','info','debug','trace'])console[name]=()=>{};
          const root=process.argv[1],bridge=fs.readFileSync(process.argv[2],'utf8');
          const start=bridge.indexOf('function qqLoginResult('),end=bridge.indexOf('function qqState(',start);
          if(start<0||end<0)throw Error('production classifier not found');
          const context={fail:()=>{throw Error('invalid classifier input');}};
          vm.createContext(context);vm.runInContext(bridge.slice(start,end)+';this.classify=qqLoginResult;',context);
          const auth=path.join(root,'dist/src/services/auth');
          const sdk=require(path.join(auth,'qrLogin.js'));
          const createDevice=require(path.join(auth,'deviceContext.js')).createAndroidDevice;
          const cases=[
            ['poll-network','qr-poll','connection-reset','error','network'],
            ['exchange-network','credential-validation','connection-reset','error','network'],
            ['missing-credential','credential-payload','missing-credential','error','invalid_response'],
            ['timeout','qr-event','qr-timeout','expired',undefined],
            ['canceled','qr-event','user-canceled','cancelled',undefined],
            ['loginFailed','qr-event','login-rejected','error','auth_required']
          ];
          (async()=>{
            let requests=0;
            for(const [kind,stage,reason,state,category] of cases){
              const device={...createDevice(),qimei:'synthetic-qimei',qimei36:'synthetic-qimei36',qimeiSavedAt:Date.now()};
              const service=sdk.createQrLoginService({
                deviceRepository:{kind:'fixture',load:()=>device,save:()=>{},clear:()=>{}},
                http:{post:async(url,body)=>{requests++;
                  if(body.req_0 && body.req_0.method==='GetSession')return {status:200,data:{code:0,req_0:{code:0,data:{session:{uid:'fixture-uid',sid:'fixture-sid',vkey:1}}}}};
                  throw Object.assign(new Error('Cookie=synthetic-private'),{code:'ECONNRESET'});
                }},
                drivers:{qq:{mode:'pull',close:()=>{},
                  createQr:async()=>({identifier:'fixture-qr',imageUrl:'fixture'}),
                  advance:async()=>{
                    if(kind==='poll-network')throw Object.assign(new Error('Token=synthetic-private'),{code:'ECONNRESET'});
                    if(kind==='exchange-network')return [{type:'cookies',payload:{cookies:{qqmusic_uin:{value:'12345'},qqmusic_key:{value:'synthetic-private'}}}}];
                    if(kind==='missing-credential')return [{type:'cookies',payload:{}}];
                    return [{type:kind,payload:null}];
                  }
                }}
              });
              const key=await service.createSession('qq');await service.createQr(key);
              let body;for(let n=0;n<4;n++){body=await service.checkQr(key,10);if(body.code===800)break;}
              assert.equal(body.code,800,'pinned SDK terminal state changed');
              assert.equal(body.failureStage,stage,'pinned SDK stage contract changed');
              assert.equal(body.failureReason,reason,'pinned SDK reason contract changed');
              const safe=context.classify(body);
              assert.equal(safe.state,state);assert.equal(safe.failure_category,category);
              if(state==='error')assert.equal(safe.failure_stage,'poll_login');
              assert.ok(!JSON.stringify(safe).includes('synthetic-private'),'private SDK error text escaped');
              await service.logout('');
            }
            process.stdout.write(JSON.stringify({ok:true,cases:cases.length,injected_http_requests:requests,external_network:false})+'\n');
          })().catch(()=>{process.stderr.write('pinned SDK classification regression\n');process.exitCode=1;});
        '''
        result=subprocess.run([str(node),"-e",script,str(module),str(root/"native/online_music/bridge.cjs")],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        evidence=json.loads(result.stdout.splitlines()[-1])
        self.assertTrue(evidence["ok"])
        self.assertEqual(evidence["cases"],6)
        self.assertFalse(evidence["external_network"])

    def test_real_sdk_reuses_device_but_keeps_fresh_services_and_bounded_http(self):
        root=Path(__file__).resolve().parents[1]
        node=root/"build/online-music/node.exe"
        module=root/"build/online-music/node_modules/@yakult-green-tea/qq-music-api"
        if not node.is_file() or not module.is_dir():self.skipTest("Pinned SDK unavailable")
        script=r'''
          const fs=require('fs'),path=require('path'),vm=require('vm'),http=require('http');
          const root=process.argv[1],bridge=fs.readFileSync(process.argv[2],'utf8');
          const start=bridge.indexOf('function createQqService('),end=bridge.indexOf('function qqState(',start);
          if(start<0||end<0)throw Error('production factory not found');
          const auth=path.join(root,'dist/src/services/auth');
          const axiosPath=require.resolve('axios',{paths:[root]}),axios=require(axiosPath);
          const createDevice=require(path.join(auth,'deviceContext.js')).createAndroidDevice;
          let qimei=0,sessions=0,seen=[],delay=false;
          const server=http.createServer((req,res)=>{let raw='';req.on('data',x=>raw+=x);req.on('end',()=>{
            const body=JSON.parse(raw);let response;
            if(body.req_0){sessions++;response={code:0,req_0:{code:0,data:{session:{uid:'synthetic-device-uid',sid:'synthetic-device-sid',vkey:1}}}};}
            else{qimei++;response={code:0,data:{code:0,data:{q16:'synthetic-qimei',q36:'synthetic-qimei36'}}};}
            setTimeout(()=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify(response));},delay?250:0);
          });});
          function make(seed){
            const localRequire=name=>name===axiosPath?{request:config=>{seen.push({timeout:config.timeout,signal:!!config.signal});return axios.request({...config,url:'http://127.0.0.1:'+server.address().port,proxy:false});}}:require(name);
            localRequire.resolve=require.resolve;
            const context={path,crypto:require('crypto'),moduleLoad:f=>f(),moduleRoot:path.dirname(path.dirname(root)),
              require:localRequire,qqDeviceContext:null,activeRequest:{deadline:Date.now()+1500,controller:new AbortController()},
              remainingTimeout:()=>Math.max(1,Math.min(12000,context.activeRequest.deadline-Date.now()))};
            vm.createContext(context);vm.runInContext(bridge.slice(start,end)+';this.create=createQqService;',context);
            return context;
          }
          (async()=>{try{
            await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
            const seed={...createDevice(),qimei:'synthetic-qimei',qimei36:'synthetic-qimei36',qimeiSavedAt:Date.now()};
            const context=make(),first=context.create(root,{device_context:seed});
            await first.createSession('qq');
            const second=context.create(root,null);await second.createSession('qq');
            if(first===second||qimei!==0||sessions!==2)throw Error('device not reused separately from sessions');
            const stored=JSON.parse(JSON.stringify(context.qqDeviceContext));
            const restarted=make();await restarted.create(root,{device_context:stored}).createSession('qq');
            if(qimei!==0||sessions!==3)throw Error('protected device was not restored');
            const invalid=make();await invalid.create(root,{device_context:{invalid:true}}).createSession('qq');
            if(qimei!==1)throw Error('invalid context was not replaced');
            const slow=make();slow.activeRequest.deadline=Date.now()+80;delay=true;
            const controller=slow.activeRequest.controller;const timer=setTimeout(()=>controller.abort(),50);
            let rejected=false;try{await slow.create(root,{device_context:stored}).createSession('qq');}catch(_){rejected=true;}finally{clearTimeout(timer);}
            if(!rejected||seen.some(x=>x.timeout<=0||x.timeout>1500||!x.signal))throw Error('HTTP cancellation/budget not propagated');
            console.log(JSON.stringify({ok:true,qimei_requests:qimei,session_requests:sessions,device_reused:true,new_resolvers:true,bounded_http:true,cancelled_http:rejected}));
          }finally{server.close();}})().catch(error=>{console.error(error.message);process.exitCode=1;});
        '''
        result=subprocess.run([str(node),"-e",script,str(module),str(root/"native/online_music/bridge.cjs")],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        evidence=json.loads(result.stdout.splitlines()[-1])
        self.assertTrue(evidence["ok"])
        self.assertTrue(evidence["cancelled_http"])


if __name__ == "__main__":unittest.main()
