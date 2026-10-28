"""Versioned adapter for the event interface verified on NetEase 3.1.42.

Unknown versions use this feature-probed adapter. No timer reads player state:
position arrives only through native callbacks, coalesced to 10 Hz. A separate
one-second heartbeat carries no player data. No install-directory file is used.
"""
from __future__ import annotations

from dataclasses import dataclass

BINDING_NAME = '__gluspotNeteaseSample'
ADAPTER_GLOBAL = '__gluspotNeteaseV1'


@dataclass(frozen=True)
class JsAdapter:
    name: str = 'legacy-native-v1'

    def install_expression(self) -> str:
        return _INSTALL

    def read_expression(self) -> str:
        return f'globalThis.{ADAPTER_GLOBAL}.read()'

    def seek_expression(self, track_id, request_id, seconds) -> str:
        import json
        arguments = ','.join(json.dumps(value, allow_nan=False) for value in (str(track_id), str(request_id), float(seconds)))
        return f'globalThis.{ADAPTER_GLOBAL}.seek({arguments})'

    def close_expression(self) -> str:
        return f'globalThis.{ADAPTER_GLOBAL} && globalThis.{ADAPTER_GLOBAL}.close()'


class JsAdapterRegistry:
    """Only a verified interface family; version hints never bypass probe."""

    @staticmethod
    def select(version: str = '', requested: str = 'auto') -> JsAdapter:
        if requested not in {'auto', 'legacy-native-v1'}:
            raise ValueError('unsupported NetEase JS adapter')
        return JsAdapter()


_INSTALL = r"""(() => {
  const name = '__gluspotNeteaseV1';
  if (globalThis[name]) return globalThis[name].start();
  const native = typeof legacyNativeCmder !== 'undefined' ? legacyNativeCmder : null;
  const seekChannel = typeof channel !== 'undefined' ? channel : null;
  if (!native || typeof native.appendRegisterCall !== 'function')
    return {api:false, reason:'native-events-unavailable'};
  const canSeek = !!seekChannel && typeof seekChannel.call === 'function';
  let active = false, heartbeat = null, latest = null, playing = null;
  let lastSent = -Infinity, sequence = 0, forceNext = true;
  const emit = value => {
    if (!active) return;
    try { globalThis.__gluspotNeteaseSample(JSON.stringify(value)); } catch (_) {}
  };
  const forward = immediate => {
    if (!latest) return;
    const now = performance.now();
    if (immediate || forceNext || now - lastSent >= 100) {
      forceNext = false; lastSent = now;
      emit({...latest, type:'sample'});
    }
  };
  const state = values => {
    for (const value of values) {
      if (typeof value === 'boolean') return value;
      if (value && typeof value.playing === 'boolean') return value.playing;
      if (typeof value === 'string') {
        if (['playing','play'].includes(value.toLowerCase())) return true;
        if (['paused','pause','stopped','stop'].includes(value.toLowerCase())) return false;
      }
    }
    return null; // Numeric PlayState meanings are not yet verified.
  };
  native.appendRegisterCall('PlayProgress','audioplayer',(trackId, seconds, ratio) => {
    if (!active) return;
    const position = Number(seconds), fraction = Number(ratio);
    if (trackId == null || !Number.isFinite(position) || position < 0) return;
    const changed = !latest || String(trackId) !== latest.trackId;
    const duration = fraction > 0 && fraction <= 1 ? position / fraction :
      (!changed && latest ? latest.duration : 0);
    latest = {trackId:String(trackId),nativeTrackId:trackId,position,duration,playing,rate:1,
              event:'PlayProgress',sequence:++sequence};
    forward(changed);
  });
  for (const event of ['PlayState','Load','End']) {
    native.appendRegisterCall(event,'audioplayer',(...args) => {
      if (!active) return;
      if (event === 'PlayState') {
        const value = state(args);
        if (value !== null) playing = value;
      } else if (event === 'End') playing = false;
      if (event === 'Load') { latest = null; playing = null; emit({type:'reset'}); }
      forceNext = true;
      if (latest) {
        latest = {...latest,playing,event};
        // Keep native position sequence: state is not a seek readback.
        forward(true);
      }
    });
  }
  const adapter = {
    start() {
      active = true; forceNext = true;
      if (!heartbeat) heartbeat = setInterval(() => emit({type:'heartbeat'}), 1000);
      return {api:true,sampleHz:10,seek:canSeek,seekWhilePaused:null,
              trackId:latest ? latest.trackId : null,adapter:'legacy-native-v1'};
    },
    read() { return latest; },
    seek(trackId, requestId, seconds) {
      if (!active || !canSeek || !latest || latest.trackId !== String(trackId) ||
          !Number.isFinite(seconds) || seconds < 0) return false;
      forceNext = true;
      seekChannel.call('audioplayer.seek',()=>{},[latest.nativeTrackId,requestId,seconds]);
      return true;
    },
    close() { active = false; clearInterval(heartbeat); heartbeat = null; }
  };
  globalThis[name] = adapter;
  return adapter.start();
})()"""
