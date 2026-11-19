import test from 'node:test';
import assert from 'node:assert/strict';
import { packedAlphaFor } from '../src/alphaSupport.js';

const UA = {
  chrome: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
  edge: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0',
  android: 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Mobile Safari/537.36',
  macSafari: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.1 Safari/605.1.15',
  iPhoneSafari: 'Mozilla/5.0 (iPhone; CPU iPhone OS 26_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.1 Mobile/15E148 Safari/604.1',
  iPhoneChrome: 'Mozilla/5.0 (iPhone; CPU iPhone OS 26_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/142.0.0.0 Mobile/15E148 Safari/604.1',
  iPadDesktop: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.1 Safari/605.1.15',
  firefox: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) Gecko/20100101 Firefox/145.0',
};

test('Chromium keeps the alpha WebM', () => {
  for (const name of ['chrome', 'edge', 'android']) assert.equal(packedAlphaFor(UA[name]), false, name);
});

test('Safari, every iOS browser and the rest get the packed MP4', () => {
  for (const name of ['macSafari', 'iPhoneSafari', 'iPhoneChrome', 'iPadDesktop', 'firefox']) assert.equal(packedAlphaFor(UA[name]), true, name);
});

test('the alpha query forces a path', () => {
  assert.equal(packedAlphaFor(UA.chrome, '?alpha=packed'), true);
  assert.equal(packedAlphaFor(UA.macSafari, '?alpha=webm'), false);
  assert.equal(packedAlphaFor(UA.chrome, '?alpha=other'), false);
});
