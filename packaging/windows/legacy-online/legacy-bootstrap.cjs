"use strict";
// This file is loaded only by the separate official Node 13.14 Win7 payload.
// It changes no Windows API/kernel behavior and does not change HTTP/TLS policy.
const Module = require("module");
const crypto = require("crypto");
const fs = require("fs");
const originalResolve = Module._resolveFilename;
const builtins = new Set(Module.builtinModules);
Module._resolveFilename = function (request, parent, isMain, options) {
  const plain = typeof request === "string" && request.startsWith("node:") ? request.slice(5) : request;
  return originalResolve.call(this, builtins.has(plain) ? plain : request, parent, isMain, options);
};
for (const feature of ["array/at", "string/at", "typed-array/at", "string/replace-all", "object/has-own", "promise/any"]) {
  require("core-js/actual/" + feature);
}
const abort = require("abort-controller");
if (!globalThis.AbortController) globalThis.AbortController = abort.AbortController;
if (!globalThis.AbortSignal) globalThis.AbortSignal = abort.AbortSignal;
const webcrypto = new (require("@peculiar/webcrypto").Crypto)();
function randomUUID() {
  const bytes = crypto.randomBytes(16);
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = bytes.toString("hex");
  return hex.slice(0, 8) + "-" + hex.slice(8, 12) + "-" + hex.slice(12, 16) + "-" + hex.slice(16, 20) + "-" + hex.slice(20);
}
webcrypto.randomUUID = randomUUID;
globalThis.crypto = webcrypto;
if (!crypto.randomUUID) crypto.randomUUID = randomUUID;
// RFC 5869 extract+expand for the pinned QQ encrypted-session module. The
// application still supplies its existing protected memory-only repository.
if (!crypto.hkdfSync) crypto.hkdfSync = function (digest, ikm, salt, info, length) {
  if (!Number.isSafeInteger(length) || length < 0) throw new RangeError("Invalid HKDF output length");
  const hashLength = crypto.createHash(digest).digest().length;
  if (length > 255 * hashLength) throw new RangeError("HKDF output length exceeds RFC 5869");
  const prk = crypto.createHmac(digest, salt).update(ikm).digest();
  const parts = [];
  let previous = Buffer.alloc(0);
  for (let index = 1; index <= Math.ceil(length / hashLength); index += 1) {
    previous = crypto.createHmac(digest, prk).update(previous).update(info).update(Buffer.from([index])).digest();
    parts.push(previous);
  }
  return Buffer.concat(parts).slice(0, length);
};
if (!fs.rmSync) fs.rmSync = function (filename, options) {
  options = options || {};
  let state;
  try { state = fs.lstatSync(filename); }
  catch (error) { if (options.force && error.code === "ENOENT") return; throw error; }
  if (state.isDirectory()) {
    if (!options.recursive) { const error = new Error("Directory removal requires recursive:true"); error.code = "ERR_FS_EISDIR"; throw error; }
    fs.rmdirSync(filename, {recursive: true});
  } else fs.unlinkSync(filename);
};
