// Offline model of the confirmed single-slot native registration semantics.
// `existing` models a player that already routes its own callbacks via WebSDK;
// false models the reported clean 3.1.42 page (UI callback outside WebSDK).
function makeNativeFixture({existing = true} = {}) {
  const events = ['PlayProgress', 'PlayState', 'Load', 'End'];
  const nativeSlots = {}, originals = {}, registered = {}, calls = [], registrations = [];
  const legacyNativeCmder = {
    registerCallMap: {}, registerCallStubs: {},
    registerCallAdapter(key, callback, namespace) {
      registrations.push({key, namespace});
      nativeSlots[key] = callback; // One slot: this DISPLACES its native owner.
      this.registerCallStubs[key] = callback;
    },
    _registerCallQueue(key) {
      const owner = this;
      return function(...args) {
        let result;
        for (const callback of owner.registerCallMap[key])
          result = Reflect.apply(callback, this, args);
        return result;
      };
    },
    appendRegisterCall(event, namespace, callback) {
      const key = namespace + '.on' + event;
      if (!this.registerCallMap[key]) {
        this.registerCallMap[key] = [];
        this.registerCallAdapter(key, this._registerCallQueue(key), 'websdk');
      }
      this.registerCallMap[key].push(callback);
    }
  };
  for (const event of events) {
    const key = 'audioplayer.on' + event;
    originals[event] = function(...args) {
      calls.push({event, receiver:this, args});
      return 'native-' + event;
    };
    nativeSlots[key] = originals[event];
    registered[event] = function(...args) { return Reflect.apply(nativeSlots[key], this, args); };
    if (existing) {
      legacyNativeCmder.registerCallMap[key] = [originals[event]];
      const stub = legacyNativeCmder._registerCallQueue(key);
      legacyNativeCmder.registerCallStubs[key] = stub;
      nativeSlots[key] = stub;
    }
  }
  return {events, legacyNativeCmder, nativeSlots, originals, registered, calls, registrations};
}
