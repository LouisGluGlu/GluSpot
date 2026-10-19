"""Actual stdio child with inert modules for favorite/write boundary checks."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from api.online_music import OnlineMusicError
from api.online_music_bridge import NodeOnlineMusicBackend


class FavoriteBridgeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        node = shutil.which("node")
        if not node:
            self.skipTest("Node unavailable for actual-child favorite checks")
        self.modules = Path(self.folder.name) / "node_modules"
        self.root = self.modules / "@neteasecloudmusicapienhanced" / "api"
        self.root.mkdir(parents=True)
        (self.root / "package.json").write_text('{"version":"4.41.1"}', encoding="utf-8")
        self.backend = NodeOnlineMusicBackend(node, self.modules, timeout_seconds=2)
        self.addCleanup(self.close_backend)
        self.files({
            "util/request.js": "module.exports=()=>{};",
            "module/login_status.js": "module.exports=async()=>({body:{data:{code:200,profile:{userId:42}}}});",
            "module/likelist.js": "module.exports=async p=>{if(p.uid!=='42')throw Error();return {body:{code:200,ids:[9,'12']}}};",
            "module/like.js": "module.exports=async p=>{if(p.id!=='9'||!['true','false'].includes(p.like))throw Error();return {body:{code:200}}};",
            "module/playlist_detail.js": "module.exports=async p=>({body:{code:200,playlist:{id:p.id,creator:{userId:42}}}});",
            "module/playlist_tracks.js": "module.exports=async p=>{if(p.op!=='add'||p.pid!=='12'||p.tracks!=='9')throw Error();return {body:{body:{code:200},status:200}}};",
            "module/user_playlist.js": "module.exports=async p=>({body:{code:200,playlist:[{id:12,name:'own',creator:{userId:42}}],more:p.offset===0}});",
        })
        self.credential = {"cookie": {"MUSIC_U": "synthetic"}}

    def files(self, files):
        for name, content in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def close_backend(self):
        processes = [self.backend._process, *(child._process for child in self.backend._authentication_backends.values())]
        self.backend.close()
        for process in processes:
            if process is not None:
                process.wait(timeout=3)

    def request(self, operation, **params):
        return self.backend.request("netease", operation, {"user_id": "42", **params}, self.credential)["data"]

    def error_code(self, operation, code, **params):
        with self.assertRaises(OnlineMusicError) as raised:
            self.request(operation, **params)
        self.assertEqual(raised.exception.code, code)

    def test_real_status_and_explicit_false_use_verified_pinned_contract(self):
        self.assertEqual(self.request("favorite_status", song_id="9"), {"liked": True})
        self.assertEqual(self.request("favorite_status", song_id="10"), {"liked": False})
        for liked in (True, False):
            self.assertEqual(self.request("set_favorite", song_id="9", liked=liked), {"ok": True, "liked": liked})

    def test_owned_playlist_detail_is_checked_before_nested_successful_write(self):
        self.assertEqual(self.request("add_song_to_playlist", song_id="9", playlist_id="12"), {"ok": True})
        page = self.request("writable_playlists", limit=2, offset=0)
        self.assertEqual(page["playlist"][0]["creator"], {"userId": 42})
        self.assertTrue(page["more"])

    def test_account_mismatch_never_calls_like_write(self):
        self.files({"module/like.js": "module.exports=async()=>{throw Error('write was reached')};"})
        self.error_code("set_favorite", "account_changed", song_id="9", liked=True, user_id="99")

    def test_foreign_playlist_never_reaches_write(self):
        self.files({
            "module/playlist_detail.js": "module.exports=async p=>({body:{code:200,playlist:{id:p.id,creator:{userId:99}}}});",
            "module/playlist_tracks.js": "module.exports=async()=>{throw Error('write was reached')};",
        })
        self.error_code("add_song_to_playlist", "not_writable", song_id="9", playlist_id="12")

    def test_denials_and_uncertain_success_never_become_heart_success(self):
        self.files({"module/like.js": "module.exports=async()=>({body:{code:500}});"})
        self.error_code("set_favorite", "mutation_failed", song_id="9", liked=True)
        self.files({"module/playlist_tracks.js": "module.exports=async()=>({body:{body:{code:301},status:200}});"})
        self.error_code("add_song_to_playlist", "auth_required", song_id="9", playlist_id="12")

    def test_invalid_status_ids_are_errors_instead_of_unliked(self):
        self.files({"module/likelist.js": "module.exports=async()=>({body:{code:200,ids:['9',{}]}});"})
        self.error_code("favorite_status", "invalid_response", song_id="9")

    def test_invalid_inputs_are_rejected_before_any_account_or_write_module(self):
        self.files({"module/login_status.js": "module.exports=async()=>{throw Error('account was reached')};"})
        self.error_code("set_favorite", "invalid_response", song_id="9", liked="false")
        self.error_code("set_favorite", "invalid_response", song_id="9,10", liked=True)
        self.error_code("add_song_to_playlist", "invalid_response", song_id="9", playlist_id="../12")


if __name__ == "__main__":
    unittest.main()
