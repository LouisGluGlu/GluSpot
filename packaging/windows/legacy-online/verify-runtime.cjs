"use strict";
// Offline acceptance only: loopback HTTP and synthetic credentials, no account,
// receiver, playback, QR request or external connection is contacted.
const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");
const net = require("net");
const crypto = require("crypto");
const {spawnSync} = require("child_process");
const runtime = path.resolve(process.argv[2]);
const resultPath = process.argv[3] && path.resolve(process.argv[3]);
const modules = path.join(runtime, "node_modules");
const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), "GluSpot-Win7-online-fixture-"));
process.env.TEMP = sandbox;
process.env.TMP = sandbox;
process.env.NODE_ENV = "test";
process.env.QQ_AUTH_STATE_PATH = "memory";
process.env.GLUSPOT_ONLINE_MUSIC_MODULE_ROOT = modules;
const write = process.stdout.write.bind(process.stdout);
for (const method of ["log", "warn", "error", "info", "debug", "trace"]) console[method] = () => {};
const checked = [];
function check(name, fn) { return Promise.resolve().then(fn).then(() => checked.push(name)); }
const requireAdapter = name => require(path.join(modules, name));
let server;
async function verify() {
  assert.strictEqual(process.version, "v13.14.0");
  require(path.join(runtime, "legacy-bootstrap.cjs"));
  // Refuse any unexpected external socket even if a dependency regresses.
  const socketConnect = net.Socket.prototype.connect;
  net.Socket.prototype.connect = function () {
    const first = arguments[0];
    const options = Array.isArray(first) ? first[0] : first;
    const host = options && typeof options === "object" ? options.host : arguments[1];
    if (host && !["127.0.0.1", "::1", "localhost"].includes(host)) throw new Error("Fixture denied an external connection");
    return socketConnect.apply(this, arguments);
  };
  await check("standard-js-and-abort-semantics", () => {
    assert.strictEqual("a$a".replaceAll("a", "$$"), "$$$");
    assert.strictEqual("aba".replaceAll("a", value => value.toUpperCase()), "AbA");
    assert.throws(() => "aba".replaceAll(/a/, "x"), TypeError);
    assert.strictEqual([1, 2, 3].at(-1), 3);
    assert.strictEqual(new Uint8Array([4, 5]).at(-1), 5);
    assert.strictEqual(Object.hasOwn({a: 1}, "a"), true);
    const controller = new AbortController();
    let fired = false;
    controller.signal.addEventListener("abort", () => { fired = true; });
    controller.abort();
    assert(fired && controller.signal.aborted);
    assert(/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(crypto.randomUUID()));
  });
  await check("rfc5869-hkdf-test-case-1", () => {
    const value = crypto.hkdfSync("sha256", Buffer.alloc(22, 11), Buffer.from("000102030405060708090a0b0c", "hex"), Buffer.from("f0f1f2f3f4f5f6f7f8f9", "hex"), 42);
    assert.strictEqual(Buffer.from(value).toString("hex"), "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865");
    assert.throws(() => crypto.hkdfSync("sha256", "x", "y", "z", 8161), RangeError);
  });
  const qqDevice = requireAdapter("@yakult-green-tea/qq-music-api/dist/src/services/auth/androidDevice.js");
  await check("qq-real-adapter-aes-cbc-matches-native", async () => {
    for (const size of [0, 1, 16, 17, 64]) {
      const key = Buffer.from("0123456789abcdef"), iv = Buffer.from("fedcba9876543210"), plain = Buffer.alloc(size, 42);
      const cipher = crypto.createCipheriv("aes-128-cbc", key, iv);
      const expected = Buffer.concat([cipher.update(plain), cipher.final()]);
      assert.deepStrictEqual(await qqDevice.aesCbcEncrypt(key, iv, plain), expected);
    }
    assert.strictEqual(qqDevice.md5("GluSpot", "fixture"), crypto.createHash("md5").update("GluSpotfixture").digest("hex"));
    const envelope = await qqDevice.buildQimeiRequest(qqDevice.createAndroidDevice(), new Date("2026-10-17T00:00:00Z"));
    assert(envelope.body.qimeiParams.params && envelope.body.qimeiParams.key);
  });
  await check("qq-real-session-encryption-purpose-and-integrity", () => {
    const session = requireAdapter("@yakult-green-tea/qq-music-api/dist/src/services/auth/sessionCrypto.js");
    const plain = {fixture: "no user credential", generation: 3};
    const sealed = session.sealJson(plain, "synthetic fixture secret", session.AUTH_SESSION_STORE_PURPOSE);
    assert.deepStrictEqual(session.openJson(sealed, "synthetic fixture secret", session.AUTH_SESSION_STORE_PURPOSE), plain);
    assert.throws(() => session.openJson(sealed, "wrong fixture secret", session.AUTH_SESSION_STORE_PURPOSE));
    assert.throws(() => session.openJson(sealed, "synthetic fixture secret", "wrong purpose"));
    const altered = JSON.parse(sealed); altered.ct = Buffer.alloc(32).toString("base64");
    assert.throws(() => session.openJson(JSON.stringify(altered), "synthetic fixture secret", session.AUTH_SESSION_STORE_PURPOSE));
  });
  await check("netease-real-qr-and-song-module-with-injected-request", async () => {
    const qr = await requireAdapter("@neteasecloudmusicapienhanced/api/module/login_qr_create.js")({key: "synthetic-fixture", qrimg: true, platform: "pc"});
    assert(qr.body.data.qrimg.startsWith("data:image/png;base64,"));
    const request = async (url, data) => {
      assert.strictEqual(url, "/api/song/enhance/player/url");
      assert.strictEqual(data.ids, '["2","1"]');
      return {body: {data: [{id: 1, url: "https://example.invalid/one"}, {id: 2, url: "https://example.invalid/two"}]}};
    };
    const songs = await requireAdapter("@neteasecloudmusicapienhanced/api/module/song_url.js")({id: "2,1", cookie: {}}, request);
    assert.deepStrictEqual(songs.body.data.map(row => row.id), [2, 1]);
  });
  await check("kugou-real-stream-module-with-injected-request", async () => {
    const response = await requireAdapter("kugoumusicapi/module/song_url.js")({hash: "ABCDEF1234", quality: "128", cookie: {}}, async options => {
      assert.strictEqual(options.url, "/v5/url");
      assert.strictEqual(options.params.hash, "abcdef1234");
      return {body: {status: 1, data: {url: "https://example.invalid/fixture"}}};
    });
    assert.strictEqual(response.body.status, 1);
  });
  const responses = [];
  server = http.createServer((request, response) => {
    if (request.url === "/redirect") { response.writeHead(302, {Location: "/result", "Set-Cookie": "fixture_session=synthetic; Path=/; HttpOnly"}); response.end(); }
    else if (request.url === "/slow") { setTimeout(() => { if (!response.destroyed) response.end("late"); }, 60); }
    else { responses.push(request.headers.cookie || ""); response.writeHead(200, {"Content-Type": "application/json"}); response.end(JSON.stringify({ok: true, path: request.url})); }
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const base = "http://127.0.0.1:" + server.address().port;
  await check("axios-real-http-redirect-json-and-cancel", async () => {
    const axios = requireAdapter("axios");
    const response = await axios.get(base + "/redirect", {proxy: false, timeout: 1000});
    assert.deepStrictEqual(response.data, {ok: true, path: "/result"});
    const controller = new AbortController();
    const pending = axios.get(base + "/slow", {proxy: false, signal: controller.signal});
    controller.abort();
    await assert.rejects(pending, error => error.code === "ERR_CANCELED");
  });
  await check("qq-real-http-client-manual-redirect-cookie", async () => {
    const axios = requireAdapter("axios");
    const {createAuthHttpClient} = requireAdapter("@yakult-green-tea/qq-music-api/dist/src/services/auth/httpClient.js");
    const client = createAuthHttpClient(axios.create({proxy: false}));
    const response = await client.post(base + "/redirect", {fixture: true});
    assert.strictEqual(response.data.ok, true);
    assert(responses.includes("fixture_session=synthetic"));
  });
  await check("real-stdio-bridge-three-platform-offline-preload", () => {
    const requests = ["netease", "qq", "kugou"].map((source, index) => ({id: index + 1, source, operation: "prepare_login", params: {}}));
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/(COOKIE|TOKEN|SECRET)/i.test(key)));
    const result = spawnSync(process.execPath, [path.join(runtime, "bridge.cjs")], {env, cwd: sandbox, input: requests.map(row => JSON.stringify(row) + "\n").join(""), encoding: "utf8", timeout: 15000, windowsHide: true});
    assert.strictEqual(result.status, 0, JSON.stringify({status: result.status, signal: result.signal,
      error: result.error ? {code: result.error.code, message: result.error.message} : null,
      stderr: result.stderr}));
    const rows = result.stdout.trim().split("\n").map(row => JSON.parse(row));
    assert.strictEqual(rows.length, 3);
    for (const row of rows) { assert.strictEqual(row.ok, true); assert.deepStrictEqual(row.data, {ready: true}); assert.strictEqual(row.credential, null); }
  });
}
verify().then(() => {
  const report = {ok: true, node: process.version, checks: checked, external_connections: false, actual_windows_7_verified: false};
  if (resultPath) fs.writeFileSync(resultPath, JSON.stringify(report, null, 2) + "\n");
  write(JSON.stringify(report) + "\n");
}).catch(error => { process.exitCode = 1; process.stderr.write(error.stack + "\n"); }).finally(() => {
  if (server) server.close();
  fs.rmdirSync(sandbox, {recursive: true});
});
