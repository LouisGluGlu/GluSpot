from __future__ import annotations

import json
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
        self.addCleanup(self.backend.close)

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
        self.assertNotIn("synthetic-secret", repr(self.backend._process.args))

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
        while self.backend._process is None and time.monotonic() < deadline:
            time.sleep(.005)
        process = self.backend._process
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
        self.assertIsNone(self.backend._process)

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
