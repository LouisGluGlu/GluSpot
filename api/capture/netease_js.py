"""Versioned adapter for the event interface verified on NetEase 3.1.42.

Unknown versions use this feature-probed adapter. A document-start shim
chains NetEase's first registration; the late fallback still fails closed when
no established callback chain exists because first-time registration can
displace NetEase's UI.
No timer reads player state:
position arrives only through native callbacks, coalesced to 10 Hz. A separate
one-second heartbeat carries no player data. No install-directory file is used.
"""
from __future__ import annotations

from dataclasses import dataclass

BINDING_NAME = '__gluspotNeteaseSample'
ADAPTER_GLOBAL = '__gluspotNeteaseV1'
EARLY_GLOBAL = '__gluspotNeteaseEarlyV1'


@dataclass(frozen=True)
class JsAdapter:
    name: str = 'legacy-native-v1'

    def install_expression(self) -> str:
        return _INSTALL

    def early_install_expression(self) -> str:
        return _EARLY_SHIM

    def read_expression(self) -> str:
        return f'globalThis.{ADAPTER_GLOBAL}.read()'

    def seek_expression(self, track_id, request_id, seconds) -> str:
        import json
        arguments = ','.join(json.dumps(value, allow_nan=False) for value in (str(track_id), str(request_id), float(seconds)))
        return f'globalThis.{ADAPTER_GLOBAL}.seek({arguments})'

    def close_expression(self) -> str:
        return f'globalThis.{ADAPTER_GLOBAL} && globalThis.{ADAPTER_GLOBAL}.close()'

    def health_expression(self) -> str:
        return f'globalThis.{ADAPTER_GLOBAL}.health()'


class JsAdapterRegistry:
    """Only a verified interface family; version hints never bypass probe."""

    @staticmethod
    def select(version: str = '', requested: str = 'auto') -> JsAdapter:
        if requested not in {'auto', 'legacy-native-v1'}:
            raise ValueError('unsupported NetEase JS adapter')
        return JsAdapter()


_EARLY_SHIM = r"""(() => {
  'use strict';
  const name = '__gluspotNeteaseEarlyV1';
  const events = ['PlayProgress', 'PlayState', 'Load', 'End'];
  const existing = globalThis[name];
  if (existing && existing.hookKind === 'load-time-native-chain-v1') return existing.status();
  if (existing) return {api:false, reason:'unsafe-previous-early-shim'};
  const hooks = new Map();
  const originalCalls = Object.fromEntries(events.map(event => [event, 0]));
  const registrations = [];
  const patched = new WeakMap();
  const inspected = new WeakSet();
  let listener = null;
  const keyEvent = value => {
    if (typeof value !== 'string') return null;
    const match = /^(?:audioplayer\.)?(?:on)?(PlayProgress|PlayState|Load|End)$/i.exec(value);
    if (!match) return null;
    return events.find(item => item.toLowerCase() === match[1].toLowerCase()) || null;
  };
  const registration = args => {
    let event = null, callback = -1;
    for (let index = 0; index < args.length; index++) {
      if (typeof args[index] === 'function') callback = index;
      if (!event) event = keyEvent(args[index]);
    }
    if (!event && args.some(value => value === 'audioplayer'))
      event = args.map(keyEvent).find(Boolean) || null;
    return event && callback >= 0 ? {event, callback} : null;
  };
  const wrapped = (event, original) => {
    if (typeof original !== 'function') return original;
    const prior = hooks.get(event);
    if (prior && prior.original === original) return prior.wrapper;
    const wrapper = function(...args) {
      const result = Reflect.apply(original, this, args);
      originalCalls[event]++;
      try { if (listener) listener(event, this, args); } catch (_) {}
      return result;
    };
    hooks.set(event, {original, wrapper});
    return wrapper;
  };
  const descriptor = (owner, key) => {
    let cursor = owner;
    while (cursor) {
      const value = Object.getOwnPropertyDescriptor(cursor, key);
      if (value) return {owner:cursor, descriptor:value};
      cursor = Object.getPrototypeOf(cursor);
    }
    return null;
  };
  const patch = (owner, method, path) => {
    if (!owner || (typeof owner !== 'object' && typeof owner !== 'function')) return;
    let methods = patched.get(owner);
    if (!methods) { methods = new Set(); patched.set(owner, methods); }
    if (methods.has(method)) return;
    const found = descriptor(owner, method);
    if (!found || typeof found.descriptor.value !== 'function') return;
    const original = found.descriptor.value;
    const wrapper = function(...args) {
      const item = registration(args);
      if (!item) return Reflect.apply(original, this, args);
      const next = args.slice();
      next[item.callback] = wrapped(item.event, next[item.callback]);
      registrations.push({path, method, event:item.event});
      return Reflect.apply(original, this, next);
    };
    try {
      Object.defineProperty(owner, method, {...found.descriptor, value:wrapper});
      methods.add(method);
    } catch (_) {}
  };
  const inspect = (owner, path) => {
    if (!owner || (typeof owner !== 'object' && typeof owner !== 'function')) return;
    if (inspected.has(owner)) return;
    inspected.add(owner);
    for (const method of ['registerCallAdapter', 'registerCall', 'appendRegisterCall', 'on'])
      patch(owner, method, path);
    for (const key of ['_envAdapter', 'channel', 'eventBridge']) {
      try { inspect(owner[key], path + '.' + key); } catch (_) {}
    }
  };
  const watch = key => {
    let found;
    try { found = Object.getOwnPropertyDescriptor(globalThis, key); } catch (_) { return; }
    if (found && 'value' in found) {
      try { inspect(found.value, key); } catch (_) {}
    }
    if (found && (!found.configurable || !('value' in found))) return;
    let current = found && 'value' in found ? found.value : undefined;
    try {
      Object.defineProperty(globalThis, key, {
        configurable: !found || found.configurable !== false,
        enumerable: found ? !!found.enumerable : true,
        get() { return current; },
        set(value) { current = value; inspect(value, key); }
      });
    } catch (_) {}
  };
  const state = {
    hookKind: 'load-time-native-chain-v1',
    status() {
      const caught = events.filter(event => hooks.has(event));
      return {api:caught.length === events.length, caught, registrations:registrations.slice(),
              hook:'load-time-native-chain-v1'};
    },
    activate(next) { listener = typeof next === 'function' ? next : null; return this.status(); },
    deactivate() { listener = null; },
    health() { return {...this.status(), originalCalls:{...originalCalls}}; }
  };
  Object.defineProperty(globalThis, name, {value:state, writable:false, configurable:false});
  for (const key of ['legacyNativeCmder', '_envAdapter', 'channel', 'orpheus']) watch(key);
  return state.status();
})()"""


_INSTALL = r"""(() => {
  'use strict'; // Preserve even null/undefined/primitive callback receivers.
  const name = '__gluspotNeteaseV1';
  const hookKind = 'existing-websdk-chain-v1';
  const early = globalThis.__gluspotNeteaseEarlyV1;
  if (globalThis[name]) {
    if (globalThis[name].hookKind !== hookKind)
      return {api:false, reason:'unsafe-previous-adapter'};
    return globalThis[name].start();
  }
  const native = typeof legacyNativeCmder !== 'undefined' ? legacyNativeCmder : null;
  const seekChannel = typeof channel !== 'undefined' ? channel : null;
  if (!native && !early)
    return {api:false, reason:'native-events-unavailable'};
  const canSeek = !!seekChannel && typeof seekChannel.call === 'function';
  let active = false, heartbeat = null, latest = null, playing = null, loadDuration = 0;
  let lastSent = -Infinity, sequence = 0, forceNext = true;
  const events = ['PlayProgress','PlayState','Load','End'];
  const listeners = {}, originalCalls = Object.fromEntries(events.map(event => [event, 0]));
  let hooks = [], earlyBound = false;
  // Inspect own data properties only. Unknown accessors/shapes are not evidence
  // of a safe late chain. This branch never calls appendRegisterCall or
  // registerCallAdapter: their first registration replaces a single native slot.
  const data = (owner, key) => owner && Object.getOwnPropertyDescriptor(owner, key);
  const value = (owner, key) => {
    const descriptor = data(owner, key);
    return descriptor && descriptor.value;
  };
  const prepare = () => {
    const map = value(native, 'registerCallMap'), stubs = value(native, 'registerCallStubs');
    if (!map || !stubs) return null;
    const prepared = [];
    for (const event of events) {
      const key = 'audioplayer.on' + event;
      const queue = value(map, key), stub = value(stubs, key);
      if (!Array.isArray(queue) || !queue.length || typeof stub !== 'function') return null;
      for (let i = 0; i < queue.length; i++)
        if (typeof value(queue, String(i)) !== 'function') return null;
      const index = String(queue.length - 1), descriptor = data(queue, index);
      if (!descriptor.writable) return null;
      const original = descriptor.value;
      const wrapper = function(...args) {
        // Preserve NetEase's receiver, arguments, result and exceptions. All
        // preceding queue entries and this original run before our observer.
        const result = Reflect.apply(original, this, args);
        try {
          if (active) { originalCalls[event]++; listeners[event](...args); }
        } catch (_) {} // GluSpot must never throw into NetEase's dispatcher.
        return result;
      };
      prepared.push({map,stubs,key,queue,stub,index,descriptor,wrapper});
    }
    return prepared;
  };
  const intact = () => {
    if (!active) return false;
    if (earlyBound) {
      try { return early.status().api === true; } catch (_) { return false; }
    }
    return hooks.length === events.length && hooks.every(hook =>
      value(native, 'registerCallMap') === hook.map &&
      value(native, 'registerCallStubs') === hook.stubs &&
      value(hook.map, hook.key) === hook.queue && value(hook.stubs, hook.key) === hook.stub &&
      value(hook.queue, hook.index) === hook.wrapper);
  };
  const restore = () => {
    if (earlyBound && early) {
      try { early.deactivate(); } catch (_) {}
      earlyBound = false;
    }
    for (const hook of hooks) {
      // A later player change owns its slot; never overwrite it on detach.
      try {
        if (value(hook.queue, hook.index) === hook.wrapper)
          Object.defineProperty(hook.queue, hook.index, hook.descriptor);
      } catch (_) {}
    }
    hooks = [];
  };
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
  listeners.PlayProgress = (trackId, seconds, ratio) => {
    if (!active) return;
    const position = Number(seconds), fraction = Number(ratio);
    if (trackId == null || !Number.isFinite(position) || position < 0) return;
    const changed = !latest || String(trackId) !== latest.trackId;
    if (changed && latest) loadDuration = 0;
    const duration = fraction > 0 && fraction < 1 ? position / fraction :
      (!changed && latest && latest.duration > 0 ? latest.duration : loadDuration);
    latest = {trackId:String(trackId),nativeTrackId:trackId,position,duration,playing,rate:1,
              event:'PlayProgress',sequence:++sequence};
    forward(changed);
  };
  for (const event of ['PlayState','Load','End']) {
    listeners[event] = (...args) => {
      if (!active) return;
      if (event === 'PlayState') {
        const value = state(args);
        if (value !== null) playing = value;
      } else if (event === 'End') playing = false;
      if (event === 'Load') {
        latest = null; playing = null; loadDuration = 0;
        // Only explicitly named seconds have established units.
        for (const value of args) {
          if (value && typeof value === 'object' &&
              Number.isFinite(value.duration_seconds) && value.duration_seconds > 0)
            loadDuration = value.duration_seconds;
        }
        emit({type:'reset'});
      }
      forceNext = true;
      if (latest) {
        latest = {...latest,playing,event};
        // Keep native position sequence: state is not a seek readback.
        forward(true);
      }
    };
  }
  const adapter = {
    hookKind,
    start() {
      if (active) {
        if (intact()) return this.probe();
        this.close();
        return {api:false, reason:'native-callback-chain-changed'};
      }
      active = true; forceNext = true;
      let earlyStatus = null;
      try { earlyStatus = early && early.status ? early.status() : null; } catch (_) {}
      if (earlyStatus && earlyStatus.api === true && typeof early.activate === 'function') {
        try {
          const status = early.activate((event, receiver, args) => {
            if (listeners[event]) { originalCalls[event]++; Reflect.apply(listeners[event], receiver, args); }
          });
          if (!status || status.api !== true) { active = false; return {api:false, reason:'native-callback-chain-unavailable'}; }
          earlyBound = true;
        } catch (_) { active = false; return {api:false, reason:'native-callback-chain-unavailable'}; }
      } else if (native) {
        // Preflight every event before touching any queue. The clean 3.1.42
        // page has no such queues, so capture must fall back without registering.
        try { hooks = prepare() || []; } catch (_) { hooks = []; }
        if (!hooks.length) { active = false; return {api:false, reason:'native-callback-chain-unavailable'}; }
        try {
          for (const hook of hooks)
            Object.defineProperty(hook.queue, hook.index, {...hook.descriptor,value:hook.wrapper});
        } catch (_) {
          restore(); active = false;
          return {api:false, reason:'native-callback-chain-unavailable'};
        }
      } else {
        active = false;
        return {api:false, reason:'native-callback-chain-unavailable'};
      }
      heartbeat = setInterval(() => {
        if (!intact()) {
          emit({type:'unavailable',reason:'native-callback-chain-changed'});
          this.close();
        } else emit({type:'heartbeat'});
      }, 1000);
      return this.probe();
    },
    probe() {
      return {api:true,sampleHz:10,seek:canSeek,seekWhilePaused:null,
              trackId:latest ? latest.trackId : null,adapter:'legacy-native-v1',
              hook:earlyBound ? 'load-time-native-chain-v1' : hookKind};
    },
    health() {
      let discovery = null;
      try { discovery = early && early.health ? early.health() : null; } catch (_) {}
      return {hook:earlyBound ? 'load-time-native-chain-v1' : hookKind,
              chainIntact:intact(),originalCalls:{...originalCalls},discovery};
    },
    read() { return latest; },
    seek(trackId, requestId, seconds) {
      if (!active || !canSeek || !latest || latest.trackId !== String(trackId) ||
          !Number.isFinite(seconds) || seconds < 0) return false;
      forceNext = true;
      seekChannel.call('audioplayer.seek',()=>{},[latest.nativeTrackId,requestId,seconds]);
      return true;
    },
    close() {
      active = false; clearInterval(heartbeat); heartbeat = null;
      restore(); latest = null; playing = null; loadDuration = 0;
    }
  };
  const probe = adapter.start();
  if (probe.api) {
    try { Object.defineProperty(globalThis, name, {value:adapter,writable:true,configurable:true}); }
    catch (_) {
      adapter.close();
      return {api:false, reason:'native-callback-chain-unavailable'};
    }
  }
  return probe;
})()"""
