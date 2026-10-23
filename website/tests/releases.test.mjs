import assert from "node:assert/strict";
import test from "node:test";
import { RELEASE_FALLBACK, REPO, loadLatestRelease, selectLatestRelease } from "../src/releases.js";

function release(version, overrides = {}) {
  return {
    tag_name: `v${version}`, draft: false, prerelease: true,
    published_at: "2026-10-23T13:24:29Z",
    assets: [
      { name: `GluSpot-${version}-test-Setup-x64.exe`, state: "uploaded", size: 100 },
      { name: `GluSpot-${version}-test-windows-x64.zip`, state: "uploaded", size: 200 },
    ], ...overrides,
  };
}

test("selects the highest published Windows version, including prereleases", () => {
  const selected = selectLatestRelease([
    release("6.417", { published_at: "2026-12-01T00:00:00Z", prerelease: false }),
    release("6.419"), release("6.418"), release("6.99"),
  ]);
  assert.deepEqual(selected, { version: "6.419", url: `${REPO}/releases/tag/v6.419` });
});

test("ignores drafts, source-only releases and incomplete Windows uploads", () => {
  assert.equal(selectLatestRelease([
    release("6.420", { draft: true }),
    release("6.421", { published_at: null }),
    release("6.422", { assets: [] }),
    release("6.423", { assets: release("6.423").assets.slice(0, 1) }),
    release("6.424", { assets: release("6.424").assets.map(a => ({ ...a, state: "new" })) }),
    release("6.425", { assets: release("6.425").assets.map(a => ({ ...a, size: 0 })) }),
    release("6.418"),
  ]).version, "6.418");
});

test("supports subsequent patch versions and ignores malformed responses", () => {
  assert.equal(selectLatestRelease([release("6.418"), release("6.418.1")]).version, "6.418.1");
  for (const data of [null, {}, [], [null, {}, release("../../elsewhere")]]) {
    assert.deepEqual(selectLatestRelease(data), RELEASE_FALLBACK);
  }
});

test("loads public releases without using the stable-only latest endpoint", async () => {
  const controller = new AbortController();
  const selected = await loadLatestRelease({
    signal: controller.signal,
    fetchImpl: async (url, options) => {
      assert.match(url, /\/releases\?per_page=100$/);
      assert.equal(options.signal, controller.signal);
      assert.equal(options.cache, "no-cache");
      return { ok: true, json: async () => [release("6.418")] };
    },
  });
  assert.equal(selected.url, `${REPO}/releases/tag/v6.418`);
});

test("API errors keep downloads on the release list instead of an old pinned build", async () => {
  for (const fetchImpl of [
    async () => { throw new Error("offline"); },
    async () => ({ ok: false, status: 403 }),
    async () => ({ ok: true, json: async () => { throw new Error("invalid JSON"); } }),
  ]) {
    assert.deepEqual(await loadLatestRelease({ fetchImpl }), RELEASE_FALLBACK);
  }
});
