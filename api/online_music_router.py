"""Send Apple Music to its companion and leave the other sources on the Node bridge."""
from __future__ import annotations


class RoutingOnlineBackend:
    def __init__(self, node=None, apple=None):
        if node is None:
            from api.online_music_bridge import NodeOnlineMusicBackend
            node = NodeOnlineMusicBackend()
        if apple is None:
            from api.apple_music_backend import AppleMusicBackend
            apple = AppleMusicBackend()
        self.node = node
        self.apple = apple

    def _backend(self, source_id: str):
        return self.apple if source_id == "apple_music" else self.node

    def available(self, source_id: str):
        return self._backend(source_id).available(source_id)

    def request(self, source_id: str, operation: str, params=None, credential=None):
        return self._backend(source_id).request(source_id, operation, params, credential)

    def prepare_login(self, source_id: str) -> bool:
        prepare = getattr(self._backend(source_id), "prepare_login", None)
        return bool(prepare(source_id)) if callable(prepare) else False

    def cancel_login_request(self, source_id: str) -> None:
        cancel = getattr(self._backend(source_id), "cancel_login_request", None)
        if callable(cancel):
            cancel(source_id)

    def close(self) -> None:
        self.node.close()
        self.apple.close()
