"""Real child transport checks for the pinned public platform response shapes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from api.online_music import OnlineMusicError
from api.online_music_bridge import NodeOnlineMusicBackend


class OnlineMusicPlatformBridgeTests(unittest.TestCase):
    def test_netease_status_requires_success_before_accepting_missing_profile_as_logout(self):
        for code, expected in ((500,"invalid_response"),(301,"auth_required"),(200,None)):
            with self.subTest(code=code):
                self.modules_for("@neteasecloudmusicapienhanced/api","4.41.1",{
                    "util/request.js":"module.exports=()=>{};",
                    "module/login_status.js":f"module.exports=async()=>({{body:{{data:{{code:{code},profile:null}}}}}});"})
                if expected:
                    with self.assertRaises(OnlineMusicError) as raised:
                        self.backend.request("netease","account_status",{},{"cookie":{"MUSIC_U":"synthetic"}})
                    self.assertEqual(raised.exception.code,expected)
                else:
                    self.assertFalse(self.backend.request("netease","account_status",{},{"cookie":{"MUSIC_U":"synthetic"}})["data"]["logged_in"])
                self.backend._authentication_backends["netease"]._stop_process()

    def test_qq_lyrics_fetches_real_qrc_contract_and_keeps_independent_lrc_fallback(self):
        self.modules_for("@yakult-green-tea/qq-music-api", "3.1.4", {
            "dist/src/services/auth/qrLogin.node.js": "exports.createNodeQrLoginService=()=>({configureAuthSessionRepository:()=>{}});",
            "dist/src/services/auth/httpClient.js": '''exports.default=()=>({
              request:async(p)=>{
                if(!p.url.includes('fcg_query_lyric_new.fcg') || p.method!=='GET' || p.params.songmid!=='mid9' || p.timeout!==2500)throw Error('wrong LRC contract');
                return {data:{code:0,lyric:'[00:01]line fallback',trans:'[00:01]译文'}};
              },
              post:async(url,p,opts)=>{
                if(opts.timeout!==2500 || p.comm.tmeAppID!=='qqmusiclight')throw Error('unbounded');
                const q=p.request;
                if(q.module==='music.pf_song_detail_svr')return {data:{code:0,request:{code:0,data:{track_info:{id:9,mid:'mid9',title:'Hello',singer:[{name:'Singer'}],album:{name:'Album'},interval:180}}}}};
                if(q.module!=='music.musichallSong.PlayLyricInfo' || q.method!=='GetPlayLyricInfo' || q.param.songID!==9 || q.param.qrc!==1 || q.param.crypt!==1 || q.param.trans!==1 || q.param.roma!==1 || Buffer.from(q.param.songName,'base64').toString()!=='Hello')throw Error('wrong QRC contract');
                return {data:{code:0,request:{code:0,data:{lyric:'encrypted-qrc',trans:'encrypted-trans',roma:'encrypted-roma'}}}};
              }
            });''',
        })
        result = self.backend.request("qq", "lyrics", {"song_id": "mid9"}, {"token": "synthetic", "sessions": []})["data"]
        self.assertEqual(result, {"raw_lrc": "[00:01]line fallback", "translation": "[00:01]译文",
            "lyric": "encrypted-qrc", "trans": "encrypted-trans", "roma": "encrypted-roma"})

    def test_qq_lyric_detail_identity_mismatch_cannot_request_another_song_qrc(self):
        self.modules_for("@yakult-green-tea/qq-music-api", "3.1.4", {
            "dist/src/services/auth/qrLogin.node.js": "exports.createNodeQrLoginService=()=>({configureAuthSessionRepository:()=>{}});",
            "dist/src/services/auth/httpClient.js": '''exports.default=()=>({
              request:async p=>{
                if(p.method!=='GET' || p.params.songmid!=='mid9')throw Error('wrong request');
                return {data:{code:0,lyric:'[00:01]retained line'}};
              },
              post:async(url,p)=>{
                if(p.request.module!=='music.pf_song_detail_svr')throw Error('must not fetch another song');
                return {data:{code:0,request:{code:0,data:{track_info:{id:10,mid:'other-song'}}}}};
              }
            });''',
        })
        result = self.backend.request("qq", "lyrics", {"song_id": "mid9"}, {"token": "synthetic", "sessions": []})["data"]
        self.assertEqual(result, {"raw_lrc": "[00:01]retained line", "translation": ""})

    def test_installed_qq_http_client_and_bridge_keep_lrc_when_qrc_is_missing_or_corrupt(self):
        """Use the real pinned client with loopback-only injected transport.

        This catches invented SDK methods hidden by a fake client. The source
        bridge function runs unchanged; no auth service or external host runs.
        """
        root = Path(__file__).resolve().parents[1]
        client = root / "build/online-music/node_modules/@yakult-green-tea/qq-music-api/dist/src/services/auth/httpClient.js"
        node = root / "build/online-music/node.exe"
        if not client.is_file() or not node.is_file():
            self.skipTest("Prepared pinned online runtime unavailable")
        script = r'''
          const fs=require('node:fs'),http=require('node:http'),vm=require('node:vm');
          const create=require(process.argv[1]).createAuthHttpClient;
          const source=fs.readFileSync(process.argv[2],'utf8');
          const start=source.indexOf('async function qqLyrics('),end=source.indexOf('function prepareLoginModules(',start);
          if(start<0 || end<0)throw Error('bridge function missing');
          const lyric=vm.runInNewContext(source.slice(start,end)+';qqLyrics',{
            Buffer,require,fail:code=>{throw Error(code);},requireId:value=>String(value)
          });
          let variant='missing',requests=[];
          const server=http.createServer((req,res)=>{
            let body='';req.on('data',part=>body+=part);req.on('end',()=>{
              const call=JSON.parse(body);requests.push(call);
              let data;
              if(call.method==='GET'){
                if(call.params.songmid!=='mid9' || !call.url.includes('fcg_query_lyric_new.fcg'))throw Error('wrong fallback');
                data={code:0,lyric:'[00:01]line fallback',trans:'[00:01]translated'};
              }else{
                const query=call.data.request;
                if(query.module==='music.pf_song_detail_svr')data={code:0,request:{code:0,data:{track_info:{id:9,mid:'mid9',title:'Hello',singer:[],album:{name:'Album'},interval:180}}}};
                else if(query.module==='music.musichallSong.PlayLyricInfo')data={code:0,request:{code:0,data:variant==='missing'?{}:{lyric:'invalid encrypted field'}}};
                else throw Error('unexpected module');
              }
              res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));
            });
          });
          const localRequest=config=>new Promise((resolve,reject)=>{
            if(config.timeout!==2500)throw Error('unbounded request');
            const request=http.request({host:'127.0.0.1',port:server.address().port,path:'/',method:'POST',headers:{'Content-Type':'application/json'}},response=>{
              let raw='';response.on('data',part=>raw+=part);response.on('end',()=>resolve({status:200,headers:{},data:JSON.parse(raw)}));
            });
            request.on('error',reject);request.end(JSON.stringify(config));
          });
          (async()=>{
            await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
            try{
              const results=[];
              for(variant of ['missing','corrupt']){
                const client=create({request:localRequest});
                if(typeof client.request!=='function' || typeof client.post!=='function' || client.get!==undefined)throw Error('unexpected pinned client shape');
                results.push(await lyric({searchHttp:client},{song_id:'mid9'}));
              }
              if(requests.length!==6 || requests.filter(call=>call.method==='GET').length!==2)throw Error('incomplete fallback chain');
              process.stdout.write(JSON.stringify({results,requests:requests.length,external_requests:0,account_used:false}));
            }finally{server.close();}
          })().catch(error=>{server.close();process.stderr.write(error.stack);process.exitCode=1;});
        '''
        result = subprocess.run([str(node), "-e", script, str(client), str(root / "native/online_music/bridge.cjs")],
            capture_output=True, text=True, encoding="utf-8", timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        from api.lyrics import LocalLyricsStore
        from api.online_music import OnlineMusicService
        # The production decode method needs only this transport boundary;
        # avoid constructing an account/credential store for an offline test.
        service = OnlineMusicService.__new__(OnlineMusicService)
        service._catalog = lambda *_args, **_kwargs: current
        store = LocalLyricsStore(Path(self.folder.name) / "lyrics")
        for current in report["results"]:
            payload = service.lyrics("qq", "mid9")
            self.assertEqual(payload.raw_qrc, "")
            match = store.parse_online_lyrics(raw_lrc=payload.raw_lrc, raw_qrc=payload.raw_qrc, source=payload.source)
            self.assertEqual(match.lyrics, ("line fallback",))
        self.assertEqual(report["external_requests"], 0)
        self.assertFalse(report["account_used"])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.node = shutil.which("node")
        if not self.node:
            self.skipTest("Node unavailable for actual-child platform checks")
        self.modules = Path(self.folder.name) / "node_modules"
        self.backend = NodeOnlineMusicBackend(self.node, self.modules, timeout_seconds=2)
        self.addCleanup(self.close_backend)

    def close_backend(self):
        backends = [self.backend, *self.backend._authentication_backends.values()]
        processes = [b._process for b in backends if b._process is not None]
        self.backend.close()
        for process in processes:
            process.wait(timeout=3)

    def modules_for(self, package, version, files):
        root = self.modules / package
        root.mkdir(parents=True, exist_ok=True)
        (root / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")
        if package == "kugoumusicapi":
            files = {"util/index.js": "exports.calculateMid=()=>1;", **files}
        for name, content in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        if package == "@yakult-green-tea/qq-music-api":
            auth = root / "dist/src/services/auth"
            auth.mkdir(parents=True, exist_ok=True)
            (auth / "qrLogin.js").write_text("exports.createQrLoginService=d=>require('./qrLogin.node.js').createNodeQrLoginService(d);exports.createMqttListen=()=>()=>{};", encoding="utf-8")
            (auth / "deviceContext.js").write_text("exports.isAndroidDevice=()=>false;", encoding="utf-8")
            client = auth / "httpClient.js"
            body = client.read_text(encoding="utf-8") if client.exists() else "exports.default=()=>({});"
            client.write_text(body + "\nexports.createAuthHttpClient=exports.createAuthHttpClient||exports.default;", encoding="utf-8")
            for name, body in {"axios": "exports.request=()=>{throw Error('fixture must not perform network I/O')};", "ws": "module.exports=class {};"}.items():
                folder = self.modules / name
                folder.mkdir(exist_ok=True)
                (folder / "index.js").write_text(body, encoding="utf-8")

    def test_qq_nested_current_profile_is_whitelisted_without_contact_metadata(self):
        self.modules_for("@yakult-green-tea/qq-music-api", "3.1.4", {
            "dist/src/services/auth/qrLogin.node.js": '''exports.createNodeQrLoginService=()=>({
              configureAuthSessionRepository:()=>{},
              getLoginStatus:async()=>({musicid:42,nickname:'stale',avatarUrl:'https://example.org/stale.png',
                info:{nick:'current fixture',logo:'https://example.org/current.png',phone:'synthetic-private',email:'synthetic-private'}})
            });''',
        })
        data = self.backend.request("qq", "account_status", {}, {"token": "synthetic", "sessions": []})["data"]
        self.assertEqual(data["nickname"], "current fixture")
        self.assertEqual(data["avatar_url"], "https://example.org/current.png")
        self.assertEqual(set(data), {"source_id", "logged_in", "user_id", "nickname", "avatar_url"})
        self.assertNotIn("synthetic-private", repr(data))

    def test_qq_profile_details_cannot_invent_a_verified_identity(self):
        self.modules_for("@yakult-green-tea/qq-music-api", "3.1.4", {
            "dist/src/services/auth/qrLogin.node.js": '''exports.createNodeQrLoginService=()=>({
              configureAuthSessionRepository:()=>{},getLoginStatus:async()=>({info:{nick:'fixture',logo:'https://example.org/current.png'}})
            });''',
        })
        data = self.backend.request("qq", "account_status", {}, {"token": "synthetic", "sessions": []})["data"]
        self.assertFalse(data["logged_in"])

    def qq_merging_repository_fixture(self):
        # Match pinned 3.1.4 AuthSessionStore.useRepository: restored incoming
        # records are overlaid by the resolver's current in-memory records.
        self.modules_for("@yakult-green-tea/qq-music-api", "3.1.4", {
            "dist/src/services/auth/qrLogin.node.js": '''exports.createNodeQrLoginService=()=>{
              let repository, sessions=new Map();
              const persist=()=>repository.save(Array.from(sessions.values()));
              return {
                configureAuthSessionRepository:repo=>{
                  const current=Array.from(sessions.values());repository=repo;
                  sessions=new Map(repo.load().map(s=>[s.token,s]));
                  for(const s of current)sessions.set(s.token,s);
                  if(current.length)persist();
                },
                createSession:async()=> 'fixture-qr',createQr:async()=> 'fixture-image',
                checkQr:async()=>{
                  sessions.set('new-token',{token:'new-token',credential:{user:2,revision:2}});persist();
                  return {code:803,cookie:'qqmusic_session=new-token;'};
                },
                getLoginStatus:async token=>{
                  const s=sessions.get(token);return s?{musicid:s.credential.user,nickname:'fixture'}:null;
                },
                getUserPlaylists:async token=>{
                  const s=sessions.get(token);if(!s)throw new Error('synthetic missing session');
                  return {v_playlist:[{tid:'fixture',dirName:'fixture',songNum:1}],revision:s.credential.revision};
                },
                logout:async token=>{if(token){sessions.delete(token);persist();}}
              };
            };''',
        })

    def test_qq_cross_lane_logout_and_new_login_do_not_retain_the_old_account(self):
        self.qq_merging_repository_fixture()
        old = {"token": "old-token", "sessions": [{"token": "old-token", "credential": {"user": 1, "revision": 1}}]}
        self.backend.request("qq", "account_status", {}, old)
        self.backend.request("qq", "playlists", {}, old)
        self.assertIsNotNone(self.backend._process)
        self.assertIsNotNone(self.backend._authentication_backends["qq"]._process)
        logout = self.backend.request("qq", "logout", {})
        self.assertEqual(logout["credential"], {"token": "", "sessions": []})
        begin = self.backend.request("qq", "begin_login", {})
        confirmed = self.backend.request("qq", "poll_login", {"key": begin["data"]["key"]})
        fresh = confirmed["credential"]
        account = self.backend.request("qq", "account_status", {}, fresh)
        self.assertEqual(account["data"]["user_id"], "2")
        catalog = self.backend.request("qq", "playlists", {}, account["credential"])
        self.assertEqual(catalog["data"]["revision"], 2)
        self.assertEqual(catalog["credential"]["token"], "new-token")
        self.assertEqual([row["token"] for row in catalog["credential"]["sessions"]], ["new-token"])

    def test_qq_fresh_same_token_envelope_overrides_old_catalog_resolver_fields(self):
        self.qq_merging_repository_fixture()
        old = {"token": "same-token", "sessions": [{"token": "same-token", "credential": {"user": 1, "revision": 1}}]}
        refreshed = {"token": "same-token", "sessions": [{"token": "same-token", "credential": {"user": 1, "revision": 2}}]}
        self.assertEqual(self.backend.request("qq", "playlists", {}, old)["data"]["revision"], 1)
        verified = self.backend.request("qq", "account_status", {}, refreshed)
        catalog = self.backend.request("qq", "playlists", {}, verified["credential"])
        self.assertEqual(catalog["data"]["revision"], 2)
        self.assertEqual(catalog["credential"]["sessions"][0]["credential"]["revision"], 2)

    def test_kugou_nested_profile_uses_display_fields_and_auth_cookie_identity(self):
        self.modules_for("kugoumusicapi", "1.6.0", {
            "util/request.js": "module.exports.createRequest=async()=>{};",
            "module/user_detail.js": '''module.exports=async p=>({body:{status:'1',error_code:'0',data:{
              user_info:{nick_name:'fixture',sizable_avatar:'//example.org/{size}/avatar.png'},phone:'synthetic-private'}}});''',
        })
        data = self.backend.request("kugou", "account_status", {}, {"cookie": {"userid": "42", "token": "synthetic"}})["data"]
        self.assertTrue(data["logged_in"])
        self.assertEqual(data["user_id"], "42")
        self.assertEqual(data["nickname"], "fixture")
        self.assertEqual(data["avatar_url"], "//example.org/{size}/avatar.png")
        self.assertNotIn("synthetic-private", repr(data))

    def test_kugou_denied_catalog_is_an_error_instead_of_an_empty_library(self):
        self.modules_for("kugoumusicapi", "1.6.0", {
            "util/request.js": "module.exports.createRequest=async()=>{};",
            "module/user_playlist.js": "module.exports=async()=>({body:{status:'0',error_code:'152',data:{info:[]}}});",
            "module/playlist_track_all.js": "module.exports=async()=>({body:{status:'1',error_code:'152',data:{info:[]}}});",
        })
        credential = {"cookie": {"userid": "42", "token": "synthetic"}}
        for operation in ("playlists", "playlist_tracks"):
            with self.subTest(operation=operation), self.assertRaises(OnlineMusicError) as raised:
                self.backend.request("kugou", operation, {"playlist_id": "fixture", "limit": 2, "offset": 0}, credential)
            self.assertEqual(raised.exception.code, "invalid_response")

    def test_kugou_successful_catalog_keeps_upstream_page_and_global_id(self):
        self.modules_for("kugoumusicapi", "1.6.0", {
            "util/request.js": "module.exports.createRequest=async()=>{};",
            "module/user_playlist.js": '''module.exports=async p=>({body:{status:'1',error_code:'0',data:{
              list_count:8,info:[{global_collection_id:'fixture-global',name:'fixture',pic:'https://example.org/cover.png'}],page:p.page}}});''',
        })
        data = self.backend.request("kugou", "playlists", {"limit": 2, "offset": 4}, {"cookie": {"userid": "42", "token": "synthetic"}})["data"]
        self.assertEqual(data["data"]["page"], 3)
        self.assertEqual(data["data"]["info"][0]["global_collection_id"], "fixture-global")

    def test_netease_recommended_shelf_uses_pinned_personalized_and_deduplicated_real_window(self):
        self.modules_for("@neteasecloudmusicapienhanced/api", "4.41.1", {
            "util/request.js": "module.exports=()=>{};",
            "module/personalized.js": '''module.exports=async p=>{
              if(p.limit!==100 || p.cookie.MUSIC_U!=='synthetic')throw new Error('wrong contract');
              return {body:{code:200,result:[{id:7,name:'one',picUrl:'https://example.org/one.jpg'},
                {id:7,name:'duplicate'},{id:0,name:'invalid'},null,{id:8,name:'two'},{id:9,name:'three'}]}};
            };''',
        })
        credential = {"cookie": {"MUSIC_U": "synthetic"}}
        page = self.backend.request("netease", "recommended_playlists", {"limit": 1, "offset": 1}, credential)["data"]
        self.assertEqual([item["id"] for item in page["items"]], [8])
        self.assertEqual((page["total"], page["next_offset"], page["has_more"]), (3, 2, True))
        tail = self.backend.request("netease", "recommended_playlists", {"limit": 3, "offset": 3}, credential)["data"]
        self.assertEqual((tail["items"], tail["next_offset"], tail["has_more"]), ([], 3, False))

    def test_netease_recommended_denial_is_not_a_successful_empty_shelf(self):
        for code, expected in ((301, "auth_required"), (500, "invalid_response")):
            with self.subTest(code=code):
                self.modules_for("@neteasecloudmusicapienhanced/api", "4.41.1", {
                    "util/request.js": "module.exports=()=>{};",
                    "module/personalized.js": f"module.exports=async()=>({{body:{{code:{code},result:[]}}}});",
                })
                with self.assertRaises(OnlineMusicError) as raised:
                    self.backend.request("netease", "recommended_playlists", {"limit": 30, "offset": 0}, {"cookie": {"MUSIC_U": "synthetic"}})
                self.assertEqual(raised.exception.code, expected)
                self.backend._stop_process()

    def qq_recommendation_modules(self, response):
        self.modules_for("@yakult-green-tea/qq-music-api", "3.1.4", {
            "dist/src/services/auth/qrLogin.node.js": "exports.createNodeQrLoginService=()=>({configureAuthSessionRepository:()=>{}});",
            "dist/src/services/auth/httpClient.js": '''exports.default=()=>({post:async(url,body,options)=>{
              const r=body.recomPlaylist;
              if(url!=='https://u.y.qq.com/cgi-bin/musicu.fcg' || body.comm.ct!==24 ||
                r.module!=='playlist.HotRecommendServer' || r.method!=='get_hot_recommend' ||
                r.param.async!==1 || r.param.cmd!==2 || options.timeout!==12000 ||
                options.headers.Referer!=='https://y.qq.com/portal/player.html')throw new Error('wrong contract');
              return {data:''' + json.dumps(response) + '''};
            }});''',
        })

    def test_qq_recommended_shelf_uses_content_id_not_zero_id_and_excludes_nonplaylists(self):
        self.qq_recommendation_modules({"code": 0, "recomPlaylist": {"code": 0, "data": {"v_hot": [
            {"type": 10014, "id": 0, "content_id": 8075336924, "title": "one", "cover": "https://example.org/one.jpg"},
            {"type": 10014, "id": 0, "content_id": 8075336924, "title": "duplicate"},
            {"type": 1, "content_id": 22, "title": "not a playlist"},
            {"type": 10014, "content_id": 7981001167, "title": "two", "cover": "https://example.org/two.jpg"},
        ]}}})
        credential = {"token": "synthetic", "sessions": []}
        response = self.backend.request("qq", "recommended_playlists", {"limit": 1, "offset": 1}, credential)
        page = response["data"]
        self.assertEqual(page["items"], [{"id": 7981001167, "name": "two", "picUrl": "https://example.org/two.jpg"}])
        self.assertEqual((page["total"], page["next_offset"], page["has_more"]), (2, 2, False))
        self.assertEqual(response["credential"], credential)

    def test_qq_recommended_errors_and_malformed_shelf_do_not_silently_clear_recommendations(self):
        for response in (
            {"code": 1, "recomPlaylist": {"code": 0, "data": {"v_hot": []}}},
            {"code": 0, "recomPlaylist": {"code": 10, "data": {"v_hot": []}}},
            {"code": 0, "recomPlaylist": {"code": 0, "data": {}}},
        ):
            with self.subTest(response=response):
                self.qq_recommendation_modules(response)
                with self.assertRaises(OnlineMusicError) as raised:
                    self.backend.request("qq", "recommended_playlists", {"limit": 30, "offset": 0}, {"token": "synthetic", "sessions": []})
                self.assertEqual(raised.exception.code, "invalid_response")
                self.backend._stop_process()

    def test_recommended_shelf_rejects_out_of_bounds_bridge_inputs_before_request(self):
        self.qq_recommendation_modules({"code": 0, "recomPlaylist": {"code": 0, "data": {"v_hot": []}}})
        for params in ({"limit": 101, "offset": 0}, {"limit": 30, "offset": -1}, {"limit": True, "offset": 0}):
            with self.subTest(params=params), self.assertRaises(OnlineMusicError) as raised:
                self.backend.request("qq", "recommended_playlists", params, {"token": "synthetic", "sessions": []})
            self.assertEqual(raised.exception.code, "invalid_response")
