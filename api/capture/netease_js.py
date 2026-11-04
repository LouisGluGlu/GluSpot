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
EARLY_REVISION = 2


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

    def pause_observation_expression(self, enabled) -> str:
        # Optional chaining keeps old, already-attached adapters unsupported.
        return f'globalThis.{ADAPTER_GLOBAL}?.observePause?.({str(enabled is True).lower()})'

    def pause_capability_expression(self) -> str:
        return f'globalThis.{ADAPTER_GLOBAL}?.pauseCapability?.()'

    def pause_expression(self, track_id, request_id) -> str:
        import json
        arguments = ','.join(json.dumps(str(value)) for value in (track_id, request_id))
        return f'globalThis.{ADAPTER_GLOBAL}?.pause?.({arguments})'

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
  if (existing && existing.revision === 2 && existing.hookKind === 'load-time-native-chain-v1') return existing.status();
  if (existing) return {api:false, reason:'unsafe-previous-early-shim'};
  const hooks = new Map();
  const originalCalls = Object.fromEntries(events.map(event => [event, 0]));
  const registrations = [];
  const patched = new WeakMap();
  const callbacks = new WeakMap(), callbackProxies = new WeakMap();
  const paths = new WeakMap();
  const defineProperty = Object.defineProperty, defineProperties = Object.defineProperties;
  const setBootInterval = globalThis.setInterval, clearBootInterval = globalThis.clearInterval;
  const rootKeys = ['legacyNativeCmder', '_envAdapter', 'channel', 'orpheus'];
  const registrationMethods = ['registerCallAdapter', 'registerCall', 'appendRegisterCall'];
  const roots = new Set([globalThis]);
  try { if (globalThis.window) roots.add(globalThis.window); } catch (_) {}
  const watches = new Map(), definitionHooks = [];
  let bootTimer = null;
  let discovering = true;
  const unwatch = (root, key) => {
    const slots = watches.get(root), slot = slots && slots.get(key);
    if (!slot) return;
    slots.delete(key);
    const current = Object.getOwnPropertyDescriptor(root, key);
    if (!current || current.get !== slot.get || current.set !== slot.set) return;
    if (slot.descriptor || slot.assigned)
      Reflect.apply(defineProperty, Object, [root, key,
        {...(slot.descriptor || {writable:true, enumerable:true, configurable:true}), value:slot.value}]);
    else delete root[key];
  };
  const stopBootScan = () => {
    if (bootTimer !== null) {
      Reflect.apply(clearBootInterval, globalThis, [bootTimer]);
      bootTimer = null;
    }
    discovering = false;
    for (const [root, slots] of watches)
      for (const key of [...slots.keys()]) { try { unwatch(root, key); } catch (_) {} }
    for (const hook of definitionHooks) {
      try {
        const current = Object.getOwnPropertyDescriptor(Object, hook.method);
        if (current && current.value === hook.wrapper)
          Reflect.apply(defineProperty, Object, [Object, hook.method, hook.descriptor]);
      } catch (_) {}
    }
  };
  let listener = null;
  const keyEvent = value => {
    if (typeof value !== 'string') return null;
    const match = /^audioplayer\.on(PlayProgress|PlayState|Load|End)$/.exec(value);
    if (!match) return null;
    return events.find(item => item.toLowerCase() === match[1].toLowerCase()) || null;
  };
  const registration = (method, args) => {
    // Only the established registration signatures. Bare Load/End (and
    // audioplayer.load commands) are not native playback notifications.
    const append = method === 'appendRegisterCall';
    const event = append && args[1] === 'audioplayer' ?
      events.find(value => value === args[0]) : (!append ? keyEvent(args[0]) : null);
    const callback = append ? 2 : 1;
    return event && typeof args[callback] === 'function' ? {event, callback} : null;
  };
  const wrapped = (event, original) => {
    if (typeof original !== 'function') return original;
    const proxy = callbackProxies.get(original);
    if (proxy && proxy.event === event) return original;
    if (proxy) original = proxy.original;
    let byEvent = callbacks.get(original);
    if (!byEvent) { byEvent = new Map(); callbacks.set(original, byEvent); }
    if (byEvent.has(event)) return byEvent.get(event);
    // A callable proxy retains length/name/prototype and native registration
    // metadata. Rest-argument functions changed every callback's arity to 0.
    const wrapper = new Proxy(original, {apply(target, receiver, args) {
      const result = Reflect.apply(target, receiver, args);
      try {
        originalCalls[event]++;
        if (listener) listener(event, receiver, args);
      } catch (_) {}
      return result;
    }});
    byEvent.set(event, wrapper);
    callbackProxies.set(wrapper, {event, original});
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
    if (!methods) { methods = new Map(); patched.set(owner, methods); }
    const found = descriptor(owner, method);
    if (!found || typeof found.descriptor.value !== 'function') return;
    if (methods.get(method) === found.descriptor.value) return;
    const original = found.descriptor.value;
    const wrapper = new Proxy(original, {apply(target, receiver, args) {
      const item = registration(method, args);
      if (!item) return Reflect.apply(target, receiver, args);
      const next = args.slice();
      next[item.callback] = wrapped(item.event, next[item.callback]);
      const result = Reflect.apply(target, receiver, next);
      hooks.set(item.event, next[item.callback]);
      registrations.push({path, method, event:item.event});
      try { if (hooks.size === events.length) stopBootScan(); } catch (_) {}
      return result;
    }});
    try {
      Reflect.apply(defineProperty, Object, [owner, method, {...found.descriptor, value:wrapper}]);
      methods.set(method, wrapper);
    } catch (_) {}
  };
  const inspect = (owner, path, seen = new WeakSet()) => {
    if (!owner || (typeof owner !== 'object' && typeof owner !== 'function')) return;
    if (seen.has(owner)) return;
    seen.add(owner);
    if (!paths.has(owner)) paths.set(owner, path);
    path = paths.get(owner);
    for (const method of [...registrationMethods, 'on']) {
      try { patch(owner, method, path); } catch (_) {}
    }
    for (const key of ['_envAdapter', 'channel', 'eventBridge']) {
      try {
        const found = descriptor(owner, key);
        if (found && 'value' in found.descriptor)
          inspect(found.descriptor.value, path + '.' + key, seen);
      } catch (_) {}
    }
  };
  const watch = (root, key) => {
    if (!discovering) return;
    if (watches.has(root) && watches.get(root).has(key)) return;
    let found;
    try { found = Object.getOwnPropertyDescriptor(root, key); } catch (_) { return; }
    try { if (found && 'value' in found) inspect(found.value, key); } catch (_) {}
    if (found && (!found.configurable || !('value' in found) || !found.writable)) return;
    // Complete bridge objects need no accessor. A temporary boot setter only
    // catches the first assignment, then publishes an ordinary data property.
    if (found && found.value && registrationMethods.some(method => descriptor(found.value, method))) return;
    const slot = {descriptor:found, value:found && found.value, assigned:false};
    slot.get = () => slot.value;
    slot.set = value => {
      slot.value = value; slot.assigned = true;
      try { unwatch(root, key); } catch (_) {}
      try { inspect(value, key); } catch (_) {}
    };
    try {
      Reflect.apply(defineProperty, Object, [root, key, {
        configurable: !found || found.configurable !== false,
        enumerable: found ? !!found.enumerable : true,
        get:slot.get, set:slot.set
      }]);
      if (!watches.has(root)) watches.set(root, new Map());
      watches.get(root).set(key, slot);
    } catch (_) {}
  };
  const scan = () => {
    for (const root of roots) {
      for (const key of rootKeys) {
        try {
          const found = Object.getOwnPropertyDescriptor(root, key);
          const slot = watches.has(root) && watches.get(root).get(key);
          if (slot) inspect(slot.value, key);
          else if (found && 'value' in found) inspect(found.value, key);
        } catch (_) {}
      }
    }
  };
  const inspectDefined = (owner, path) => {
    if (paths.has(owner) || registrationMethods.some(method => {
      const found = descriptor(owner, method);
      return found && typeof found.descriptor.value === 'function';
    })) inspect(owner, path);
  };
  const defined = (owner, key) => {
    if (roots.has(owner)) {
      // A data descriptor bypasses/replaces our assignment setter. Inspect the
      // installed value synchronously, before the caller can register its UI.
      // Do not reread the caller's descriptor or coerce its property key again.
      if (typeof key === 'string' && rootKeys.includes(key)) watch(owner, key);
      else for (const rootKey of rootKeys) watch(owner, rootKey);
    } else inspectDefined(owner, paths.get(owner) || 'defined-object');
    if (typeof key === 'string') {
      const found = Object.getOwnPropertyDescriptor(owner, key);
      if (found && 'value' in found) inspectDefined(found.value, paths.get(owner) || key);
    }
  };
  const state = {
    hookKind: 'load-time-native-chain-v1',
    revision: 2,
    status() {
      const caught = events.filter(event => hooks.has(event));
      // Progress is the capture source. State/end improve it when registered;
      // 3.1.42 need not register Load during boot.
      return {api:hooks.has('PlayProgress'), caught, registrations:registrations.slice(),
              hook:'load-time-native-chain-v1', revision:2, discovering};
    },
    activate(next) { listener = typeof next === 'function' ? next : null; return this.status(); },
    deactivate() { listener = null; },
    health() { return {...this.status(), originalCalls:{...originalCalls}}; }
  };
  Reflect.apply(defineProperty, Object, [globalThis, name, {value:state, writable:false, configurable:false}]);
  for (const root of roots) for (const key of rootKeys) watch(root, key);
  for (const [method, original] of [['defineProperty', defineProperty], ['defineProperties', defineProperties]]) {
    const found = Object.getOwnPropertyDescriptor(Object, method);
    const wrapper = new Proxy(original, {apply(target, receiver, args) {
      // Restore our temporary accessor before the native descriptor operation;
      // writable-only updates must apply to the player's real data descriptor.
      try {
        if (roots.has(args[0])) {
          if (method === 'defineProperty' && typeof args[1] === 'string') unwatch(args[0], args[1]);
          else for (const key of rootKeys) { try { unwatch(args[0], key); } catch (_) {} }
        }
      } catch (_) {}
      const result = Reflect.apply(target, receiver, args);
      try { defined(args[0], method === 'defineProperty' ? args[1] : null); } catch (_) {}
      return result;
    }});
    definitionHooks.push({method, descriptor:found, wrapper});
    Reflect.apply(defineProperty, Object, [Object, method, {...found, value:wrapper}]);
  }
  // Discovery only: no player-state reads or native registration. Revisit
  // objects that gain methods/children by assignment during the first 5 s.
  const bootDeadline = Date.now() + 5000;
  let bootScans = 0;
  bootTimer = Reflect.apply(setBootInterval, globalThis, [() => {
    if (hooks.size === events.length || Date.now() >= bootDeadline || ++bootScans >= 100) {
      stopBootScan();
      return;
    }
    scan();
  }, 50]);
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
  // Dormant until t260 is explicitly enabled. Learn an actual page-originated
  // Pause contract, then require independent PlayState=false confirmation.
  // A channel with a call method alone never proves pause support.
  let pauseHook = null, pauseCandidate = null, pauseContract = null, replayingPause = false;
  const pauseRequests = new Set();
  const stopPauseObservation = () => {
    if (pauseHook) {
      try {
        if (value(seekChannel, 'call') === pauseHook.wrapper)
          Object.defineProperty(seekChannel, 'call', pauseHook.descriptor);
      } catch (_) {}
    }
    pauseHook = pauseCandidate = pauseContract = null;
    pauseRequests.clear();
  };
  const pauseAvailable = () => !!(active && intact() && latest && pauseContract && pauseHook &&
    value(seekChannel, 'call') === pauseHook.wrapper &&
    pauseContract.trackId === latest.trackId && pauseContract.nativeTrackId === latest.nativeTrackId);
  const observePause = enabled => {
    if (!enabled) { stopPauseObservation(); return true; }
    if (!active || !intact()) return false;
    if (pauseHook && value(seekChannel, 'call') === pauseHook.wrapper) return true;
    stopPauseObservation();
    const descriptor = data(seekChannel, 'call');
    if (!descriptor || !descriptor.writable || typeof descriptor.value !== 'function') return false;
    const original = descriptor.value;
    const wrapper = new Proxy(original, {apply(target, receiver, args) {
      let candidate = null;
      try {
        // An unrelated command cannot later confirm an older pending Pause.
        if (!replayingPause) pauseCandidate = null;
        // These are observed argument shapes, not synthesized native commands.
        // Opaque/request-token/multi-argument shapes remain unsupported.
        if (!replayingPause && active && latest && playing === true && receiver === seekChannel &&
            args.length === 3 && args[0] === 'audioplayer.pause' && typeof args[1] === 'function' &&
            Array.isArray(args[2]) && (args[2].length === 0 ||
              (args[2].length === 1 && value(args[2], '0') === latest.nativeTrackId))) {
          candidate = {trackId:latest.trackId,nativeTrackId:latest.nativeTrackId,
            method:args[0],args:args[2].length ? [latest.nativeTrackId] : [],at:performance.now()};
          pauseCandidate = candidate;
        }
      } catch (_) { pauseCandidate = null; }
      try { return Reflect.apply(target, receiver, args); }
      catch (error) {
        if (pauseCandidate === candidate) pauseCandidate = null;
        if (pauseContract === candidate) pauseContract = null;
        throw error;
      }
    }});
    try { Object.defineProperty(seekChannel, 'call', {...descriptor,value:wrapper}); }
    catch (_) { return false; }
    pauseHook = {descriptor,wrapper,original};
    return true;
  };
  const prepare = () => {
    const map = value(native, 'registerCallMap'), stubs = value(native, 'registerCallStubs');
    if (!map || !stubs) return null;
    const prepared = [];
    for (const event of events) {
      const key = 'audioplayer.on' + event;
      // Optional events may never acquire a queue. Any present queue must
      // still pass preflight before we touch it.
      if (event !== 'PlayProgress' && !data(map, key)) continue;
      const queue = value(map, key), stub = value(stubs, key);
      if (!Array.isArray(queue) || !queue.length || typeof stub !== 'function') return null;
      for (let i = 0; i < queue.length; i++)
        if (typeof value(queue, String(i)) !== 'function') return null;
      const index = String(queue.length - 1), descriptor = data(queue, index);
      if (!descriptor.writable) return null;
      const original = descriptor.value;
      const wrapper = new Proxy(original, {apply(target, receiver, args) {
        // Preserve NetEase's receiver, arguments, result and exceptions. All
        // preceding queue entries and this original run before our observer.
        const result = Reflect.apply(target, receiver, args);
        try {
          if (active) { originalCalls[event]++; listeners[event](...args); }
        } catch (_) {} // GluSpot must never throw into NetEase's dispatcher.
        return result;
      }});
      prepared.push({event,map,stubs,key,queue,stub,index,descriptor,wrapper});
    }
    return prepared;
  };
  const intact = () => {
    if (!active) return false;
    if (earlyBound) {
      try { return early.status().api === true; } catch (_) { return false; }
    }
    return hooks.some(hook => hook.event === 'PlayProgress') && hooks.every(hook =>
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
        if (value === false && latest && pauseCandidate &&
            pauseCandidate.trackId === latest.trackId &&
            pauseCandidate.nativeTrackId === latest.nativeTrackId &&
            performance.now() - pauseCandidate.at <= 2000) pauseContract = pauseCandidate;
        pauseCandidate = null;
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
        // Preflight progress and all present optional queues before mutation.
        // A clean 3.1.42 page has no queues: fall back without registering.
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
    observePause,
    pauseCapability() {
      return {available: !!(active && canSeek && intact() && latest && playing === true),
              trackId: latest ? latest.trackId : null};
    },
    pause(trackId, requestId) {
      // Same channel.call as seek, aimed at the track that is current now.
      // A later song is a new call with that song's native id, not a stored contract.
      if (!active || !canSeek || !intact() || !latest || playing !== true ||
          latest.trackId !== String(trackId) || typeof requestId !== 'string' ||
          !requestId || pauseRequests.has(requestId)) return false;
      pauseRequests.add(requestId);
      if (pauseRequests.size > 256) { pauseRequests.clear(); pauseRequests.add(requestId); }
      seekChannel.call('audioplayer.pause', ()=>{}, [latest.nativeTrackId]);
      return true;
    },
    seek(trackId, requestId, seconds) {
      if (!active || !canSeek || !latest || latest.trackId !== String(trackId) ||
          !Number.isFinite(seconds) || seconds < 0) return false;
      forceNext = true;
      seekChannel.call('audioplayer.seek',()=>{},[latest.nativeTrackId,requestId,seconds]);
      return true;
    },
    close() {
      stopPauseObservation();
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
