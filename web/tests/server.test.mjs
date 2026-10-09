import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createWebServer } from '../server.mjs';

async function listen(server, context) {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  context.after(
    () =>
      new Promise((resolve) => {
        server.closeAllConnections();
        server.close(resolve);
      }),
  );
  return `http://127.0.0.1:${server.address().port}`;
}
async function login(url, password = 'test-password') {
  const response = await fetch(url + '/api/login', {
    method: 'POST',
    headers: { Origin: url, 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  });
  return { response, cookie: response.headers.get('set-cookie')?.split(';')[0] };
}

test('proxy forwards catalog reads and watchlist edits to engine', async (context) => {
  const received = [];
  const engine = await listen(
    createServer(async (req, res) => {
      let body = '';
      for await (const chunk of req) body += chunk.toString();
      received.push({ method: req.method, path: req.url, body });
      res.setHeader('Content-Type', 'application/json');
      res.end('{"items":[]}');
    }),
    context,
  );
  const url = await listen(createWebServer({ engineUrl: engine }), context);
  const response = await fetch(url + '/api/stocklist');
  assert.equal(response.status, 200);
  assert.equal(
    (
      await fetch(url + '/api/watchlist', {
        method: 'POST',
        headers: { Origin: url, 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: 'THYAO' }),
      })
    ).status,
    200,
  );
  assert.equal(
    (
      await fetch(url + '/api/watchlist/THYAO', {
        method: 'DELETE',
        headers: { Origin: url },
      })
    ).status,
    200,
  );
  assert.deepEqual(received, [
    { method: 'GET', path: '/v1/web/stocklist', body: '' },
    { method: 'POST', path: '/v1/web/watchlist', body: '{"symbol":"THYAO"}' },
    { method: 'DELETE', path: '/v1/web/watchlist/THYAO', body: '' },
  ]);
});

test('login protects API, uses httpOnly cookies, and logout revokes the session', async (context) => {
  const engine = await listen(
    createServer((req, res) => res.end('{}')),
    context,
  );
  const url = await listen(
    createWebServer({ engineUrl: engine, password: 'test-password' }),
    context,
  );
  assert.equal((await fetch(url + '/api/portfolio')).status, 401);
  assert.deepEqual(await (await fetch(url + '/api/session')).json(), {
    authenticated: false,
    login_required: true,
  });
  assert.equal((await login(url, 'wrong')).response.status, 401);
  const { response, cookie } = await login(url);
  assert.equal(response.status, 200);
  assert.match(response.headers.get('set-cookie'), /HttpOnly; SameSite=Strict/);
  assert.equal((await fetch(url + '/api/portfolio', { headers: { Cookie: cookie } })).status, 200);
  await fetch(url + '/api/logout', { method: 'POST', headers: { Origin: url, Cookie: cookie } });
  assert.equal((await fetch(url + '/api/portfolio', { headers: { Cookie: cookie } })).status, 401);
});

test('proxy hides engine credentials, rejects debug paths, and streams before completion', async (context) => {
  let receivedAuthorization;
  let completed = false;
  const engine = await listen(
    createServer((req, res) => {
      receivedAuthorization = req.headers.authorization;
      assert.equal(req.url, '/v1/chat');
      res.writeHead(200, { 'Content-Type': 'application/x-ndjson' });
      res.write('{"type":"text","content":"Türkçe"}\n');
      setTimeout(() => {
        completed = true;
        res.end('{"type":"text","content":" yanıt"}\n');
      }, 150);
    }),
    context,
  );
  const url = await listen(
    createWebServer({ engineUrl: engine, engineKey: 'private-key' }),
    context,
  );
  const response = await fetch(url + '/api/chat', {
    method: 'POST',
    headers: { Origin: url },
    body: JSON.stringify({ session_id: 'web:123', message: 'test' }),
  });
  const reader = response.body.getReader();
  const first = await reader.read();
  assert.equal(completed, false);
  assert.match(new TextDecoder().decode(first.value), /Türkçe/);
  while (!(await reader.read()).done) {
    /* consume */
  }
  assert.equal(receivedAuthorization, 'Bearer private-key');
  assert.equal(response.headers.get('authorization'), null);
  assert.equal((await fetch(url + '/api/debug/state')).status, 404);
  assert.equal(
    (await fetch(url + '/api/jobs/daily-digest', { method: 'POST', headers: { Origin: url } }))
      .status,
    404,
  );
  assert.equal(
    (
      await fetch(url + '/api/chat', {
        method: 'POST',
        headers: { Origin: url },
        body: JSON.stringify({ session_id: 'external-session', message: 'x' }),
      })
    ).status,
    400,
  );
});

test('cross-origin writes and oversized requests are rejected', async (context) => {
  const url = await listen(
    createWebServer({ engineUrl: 'http://engine.test', password: 'test-password' }),
    context,
  );
  assert.equal(
    (
      await fetch(url + '/api/login', {
        method: 'POST',
        headers: { Origin: 'https://other.example' },
        body: '{}',
      })
    ).status,
    403,
  );
  assert.equal((await fetch(url + '/api/login', { method: 'POST', body: '{}' })).status, 403);
  assert.equal(
    (
      await fetch(url + '/api/login', {
        method: 'POST',
        headers: { Origin: url },
        body: JSON.stringify({ password: 'x'.repeat(70000) }),
      })
    ).status,
    413,
  );
});

test('upstream failure is reported and static asset requests never return SPA HTML', async (context) => {
  const directory = await mkdtemp(join(tmpdir(), 'trader-web-test-'));
  await writeFile(join(directory, 'index.html'), '<html>Trader Agent</html>');
  context.after(() => rm(directory, { recursive: true }));
  const url = await listen(
    createWebServer({ engineUrl: 'http://127.0.0.1:1', staticRoot: directory }),
    context,
  );
  assert.equal((await fetch(url + '/api/meta')).status, 502);
  assert.equal((await fetch(url + '/assets/missing.js')).status, 404);
  assert.match(await (await fetch(url + '/')).text(), /Trader Agent/);
  assert.match(await (await fetch(url + '/portfolio')).text(), /Trader Agent/);
});

test('cloud origin gives secure cookies and blocks login guessing after ten attempts', async (context) => {
  const url = await listen(
    createWebServer({
      engineUrl: 'http://engine.test',
      password: 'test-password',
      origin: 'https://trader.example',
    }),
    context,
  );
  const headers = { Origin: 'https://trader.example', 'Content-Type': 'application/json' };
  const successful = await fetch(url + '/api/login', {
    method: 'POST',
    headers,
    body: JSON.stringify({ password: 'test-password' }),
  });
  assert.match(successful.headers.get('set-cookie'), /; Secure$/);
  for (let i = 0; i < 10; i++) {
    assert.equal(
      (await fetch(url + '/api/login', { method: 'POST', headers, body: '{"password":"wrong"}' }))
        .status,
      401,
    );
  }
  assert.equal(
    (
      await fetch(url + '/api/login', {
        method: 'POST',
        headers,
        body: '{"password":"test-password"}',
      })
    ).status,
    429,
  );
});
