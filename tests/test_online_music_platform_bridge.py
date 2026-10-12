"""Real child transport checks for the pinned public platform response shapes."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from api.online_music import OnlineMusicError
from api.online_music_bridge import NodeOnlineMusicBackend


class OnlineMusicPlatformBridgeTests(unittest.TestCase):
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
