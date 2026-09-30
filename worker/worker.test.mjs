import assert from 'node:assert/strict';
import { test } from 'node:test';
import worker from './index.mjs';

test('authenticated streaming relay only forwards to Telegram', async () => {
  const env = { BOT_TOKEN: '123:TEST', RELAY_SECRET: 'secret' };
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return new Response('video-bytes', { headers: { 'Content-Type': 'video/mp4' } });
  };
  try {
    const headers = { 'X-Relay-Secret': 'secret', 'Content-Type': 'application/json' };
    let response = await worker.fetch(new Request('https://relay.example/api/getMe', { method: 'POST', headers, body: '{}' }), env);
    assert.equal(response.status, 200);
    assert.equal(calls[0].url, 'https://api.telegram.org/bot123:TEST/getMe');
    assert.equal(calls[0].init.headers.has('X-Relay-Secret'), false);
    assert.equal(await response.text(), 'video-bytes');
    response = await worker.fetch(new Request('https://relay.example/file/videos/file_1.mp4', { headers }), env);
    assert.equal(calls[1].url, 'https://api.telegram.org/file/bot123:TEST/videos/file_1.mp4');
    assert.equal(await response.text(), 'video-bytes');
    assert.equal((await worker.fetch(new Request('https://relay.example/api/getMe', { method: 'POST' }), env)).status, 401);
    assert.equal((await worker.fetch(new Request('https://relay.example/https://evil.example/', { headers }), env)).status, 404);
    assert.equal(calls.length, 2);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
