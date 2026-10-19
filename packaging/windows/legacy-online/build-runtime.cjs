"use strict";
// Run with the maintained build-host Node, never with the legacy runtime.
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
async function main() {
function argument(name) { const index = process.argv.indexOf(name); if (index < 0 || !process.argv[index + 1]) throw new Error("Missing " + name); return path.resolve(process.argv[index + 1]); }
const source = argument("--source"), output = argument("--output"), tooling = argument("--tooling"), legacyNode = argument("--node");
const project = path.resolve(__dirname, "../../..");
if (source === output || output.startsWith(source + path.sep) || source.startsWith(output + path.sep)) throw new Error("Legacy output must be separate from the source runtime");
if (fs.existsSync(output) && fs.readdirSync(output).length) throw new Error("Legacy output must be empty; preserve existing output before rebuilding");
const esbuild = require(path.join(tooling, "node_modules/esbuild"));
const expected = {"@neteasecloudmusicapienhanced/api": "4.41.1", "@yakult-green-tea/qq-music-api": "3.1.4", "kugoumusicapi": "1.6.0"};
for (const [name, version] of Object.entries(expected)) {
  if (JSON.parse(fs.readFileSync(path.join(source, "node_modules", name, "package.json"), "utf8")).version !== version) throw new Error("Source adapter version mismatch: " + name);
}
const lockPath = path.join(project, "native/online_music/package-lock.json");
const sourceReceipt = JSON.parse(fs.readFileSync(path.join(source, "SOURCE.json"), "utf8"));
const hash = filename => crypto.createHash("sha256").update(fs.readFileSync(filename)).digest("hex");
if (sourceReceipt.lock_sha256 !== hash(lockPath)) throw new Error("Modern source runtime receipt does not match the pinned adapter lock");
fs.mkdirSync(output, {recursive: true});
let copiedFiles = 0, transformedFiles = 0;
const inventory = [];
const excludedNativeFiles = [];
const retainedEsmFiles = [];
const transforms = new Map();
function copyTree(from, to, moduleType = "commonjs") {
  fs.mkdirSync(to, {recursive: true});
  const manifest = path.join(from, "package.json");
  if (fs.existsSync(manifest)) moduleType = JSON.parse(fs.readFileSync(manifest, "utf8")).type || "commonjs";
  for (const entry of fs.readdirSync(from, {withFileTypes: true})) {
    const input = path.join(from, entry.name), target = path.join(to, entry.name);
    if (entry.isSymbolicLink()) throw new Error("Unexpected symlink in prepared dependency tree: " + input);
    if (entry.isDirectory()) { copyTree(input, target, moduleType); continue; }
    if (!entry.isFile()) continue;
    // Upstream server-only node-windows service installers are unrelated to
    // this stdio host. Neither native addons nor extra executables are loaded
    // by its adapter contract; retain their package metadata, omit binaries.
    if (/\.(?:exe|dll|node)$/i.test(entry.name)) { excludedNativeFiles.push(path.relative(source, input).replace(/\\/g, "/")); continue; }
    const bytes = fs.readFileSync(input);
    const isEsmSource = /\.mjs$/.test(entry.name) || moduleType === "module" && /\.js$/.test(entry.name);
    if (/\.c?js$/.test(entry.name) && !isEsmSource) {
      transforms.set(target, async () => {
        const result = await esbuild.transform(bytes.toString("utf8"), {target: "node13.14", sourcefile: input, logLevel: "silent", legalComments: "inline"});
        fs.writeFileSync(target, result.code);
        transformedFiles += 1;
      });
    } else {
      // The stdio host and its tested adapter closure are CommonJS. Keep
      // upstream ESM entrypoints as source evidence without claiming they can
      // run in Node13 (for example cliui's modern module.exports named export).
      if (isEsmSource) retainedEsmFiles.push(path.relative(output, target).replace(/\\/g, "/"));
      fs.writeFileSync(target, bytes);
    }
    copiedFiles += 1;
    inventory.push({path: path.relative(output, target).replace(/\\/g, "/"), source_sha256: crypto.createHash("sha256").update(bytes).digest("hex")});
  }
}
copyTree(path.join(source, "node_modules"), path.join(output, "node_modules"));
for (const entry of fs.readdirSync(path.join(tooling, "node_modules"), {withFileTypes: true})) {
  // esbuild and its platform executables are build tools; no modern executable
  // or native addon may enter the Win7 online runtime.
  if (!entry.isDirectory() || entry.name === "esbuild" || entry.name === "@esbuild" || entry.name === ".bin") continue;
  copyTree(path.join(tooling, "node_modules", entry.name), path.join(output, "node_modules", entry.name));
}
let jobIndex = 0;
const transformJobs = Array.from(transforms.values());
await Promise.all(Array.from({length: 8}, async () => {
  while (jobIndex < transformJobs.length) { const job = transformJobs[jobIndex++]; await job(); }
}));
const bridgePath = path.join(project, "native/online_music/bridge.cjs");
fs.writeFileSync(path.join(output, "bridge-legacy-source.cjs"), esbuild.transformSync(fs.readFileSync(bridgePath, "utf8"), {target: "node13.14", sourcefile: bridgePath, logLevel: "silent", legalComments: "inline"}).code);
fs.copyFileSync(path.join(__dirname, "legacy-bootstrap.cjs"), path.join(output, "legacy-bootstrap.cjs"));
fs.writeFileSync(path.join(output, "bridge.cjs"), '"use strict";\nrequire("./legacy-bootstrap.cjs");\nrequire("./bridge-legacy-source.cjs");\n');
fs.copyFileSync(legacyNode, path.join(output, "node.exe"));
copyTree(path.join(source, "licenses"), path.join(output, "licenses"));
fs.copyFileSync(path.join(path.dirname(legacyNode), "LICENSE"), path.join(output, "licenses/Node-LICENSE.txt"));
fs.copyFileSync(lockPath, path.join(output, "package-lock.json"));
fs.copyFileSync(path.join(__dirname, "package-lock.json"), path.join(output, "legacy-package-lock.json"));
fs.copyFileSync(path.join(project, "native/online_music/package.json"), path.join(output, "package.json"));
fs.writeFileSync(path.join(output, "SOURCE-modern-adapters.json"), JSON.stringify(sourceReceipt, null, 2) + "\n");
const receipt = {
  product: "GluSpot", variant: "experimental-win7-owned-playback", node: {version: "v13.14.0", sha256: hash(path.join(output, "node.exe")), official_distribution: "https://nodejs.org/dist/v13.14.0/", license_file: "licenses/Node-LICENSE.txt", license_sha256: hash(path.join(output, "licenses/Node-LICENSE.txt")), end_of_life: true},
  target: "Windows 7 SP1 x64", windows_7_device_verified: false,
  bridge: "native/online_music/bridge.cjs", bridge_source_sha256: hash(bridgePath), lock_sha256: hash(lockPath), legacy_lock_sha256: hash(path.join(__dirname, "package-lock.json")),
  build_transform: {tool: "esbuild", version: esbuild.version, target: "node13.14", syntax_lowering_only: true, copied_files: copiedFiles, transformed_files: transformedFiles},
  packages: sourceReceipt.packages, runtime_shims: {webcrypto: "1.5.0", abort_controller: "3.0.0", core_js: "3.45.1"},
  limitations: ["Official Node 13 is end of life; this is a separately selected legacy build.", "Pinned upstream adapters still declare their modern Node support floors; transpilation and shims require separate regression evidence.", "Retained upstream ESM source entrypoints (.mjs and type:module .js) are outside the verified CommonJS stdio path and are not certified for Node13.", "Offline fixture success does not establish network login, stream playback or actual Windows 7 compatibility."],
  excluded_native_files: excludedNativeFiles,
  retained_esm_source_files: retainedEsmFiles,
  inventory: Array.from(new Map(inventory.map(item => [item.path, {...item, sha256: hash(path.join(output, item.path))}])).values()),
};
fs.writeFileSync(path.join(output, "SOURCE.json"), JSON.stringify(receipt, null, 2) + "\n");
console.log(JSON.stringify({output, copiedFiles, transformedFiles}));
}
main().catch(error => { process.stderr.write(error.stack + "\n"); process.exitCode = 1; });
