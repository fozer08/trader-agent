import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createUserUIServer } from '../server.mjs';

async function listen(server, context) {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  context.after(() => new Promise((resolve) => server.close(resolve)));
  return `http://127.0.0.1:${server.address().port}`;
}
async function call(url, path, { cookie, method = 'GET', body, origin = url } = {}) {
  return fetch(url + '/api' + path, {
    method,
    headers: {
      ...(cookie ? { Cookie: cookie } : {}),
      Origin: origin,
      'Content-Type': 'application/json',
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}
async function register(url, username = 'trader_one') {
  const response = await call(url, '/register', {
    method: 'POST',
    body: { username, password: 'test-password' },
  });
  assert.equal(response.status, 200);
  return response.headers.get('set-cookie').split(';')[0];
}

test('accounts authenticate, survive restart, and revoked cookies cannot access API', async (context) => {
  const directory = await mkdtemp(join(tmpdir(), 'trader-users-'));
  context.after(() => rm(directory, { recursive: true }));
  const server = createUserUIServer({
    engineUrl: 'http://127.0.0.1:1',
    databasePath: join(directory, 'web.db'),
  });
  const url = await listen(server, context);
  assert.equal((await call(url, '/history')).status, 401);
  assert.equal((await call(url, '/session')).status, 200);
  const cookie = await register(url);
  const profile = await (await call(url, '/session', { cookie })).json();
  assert.equal(profile.user.username, 'trader_one');
  assert.equal(profile.authenticated, true);
  const secondServer = createUserUIServer({
    engineUrl: 'http://127.0.0.1:1',
    databasePath: join(directory, 'web.db'),
  });
  const other = await listen(secondServer, context);
  assert.equal((await (await call(other, '/session', { cookie })).json()).user.id, profile.user.id);
  const login = await call(url, '/login', {
    method: 'POST',
    body: { username: 'TRADER_ONE', password: 'test-password' },
  });
  assert.equal(login.status, 200);
  assert.match(login.headers.get('set-cookie'), /HttpOnly; SameSite=Strict/);
  assert.equal(
    (
      await call(url, '/login', {
        method: 'POST',
        body: { username: 'trader_one', password: 'wrong' },
      })
    ).status,
    401,
  );
  assert.equal(
    (
      await call(url, '/register', {
        method: 'POST',
        body: { username: 'trader_one', password: 'another-password' },
      })
    ).status,
    409,
  );
  await call(url, '/logout', { cookie, method: 'POST' });
  assert.equal((await call(other, '/history', { cookie })).status, 401);
});

test('display preferences and conversation selection are user scoped and validated', async (context) => {
  const url = await listen(createUserUIServer({ engineUrl: 'http://127.0.0.1:1' }), context);
  const alice = await register(url),
    bob = await register(url, 'trader_two');
  const settings = { sidebarCollapsed: true, chatWidth: 720, markets: ['bitcoin', 'tr2y'] };
  assert.equal(
    (await call(url, '/settings', { cookie: alice, method: 'PUT', body: settings })).status,
    200,
  );
  assert.deepEqual(await (await call(url, '/settings', { cookie: alice })).json(), settings);
  assert.equal(
    (await (await call(url, '/settings', { cookie: bob })).json()).sidebarCollapsed,
    false,
  );
  assert.equal(
    (
      await call(url, '/settings', {
        cookie: alice,
        method: 'PUT',
        body: { ...settings, chatWidth: 99999 },
      })
    ).status,
    400,
  );
  const history = await (await call(url, '/history', { cookie: alice })).json();
  assert.equal(
    (
      await call(url, '/conversation', {
        cookie: bob,
        method: 'POST',
        body: { id: history.conversationId },
      })
    ).status,
    404,
  );
  await call(url, '/reset', { cookie: alice, method: 'POST', body: {} });
  assert.equal(
    (await (await call(url, '/conversations', { cookie: alice })).json()).items.length,
    2,
  );
  assert.equal((await (await call(url, '/conversations', { cookie: bob })).json()).items.length, 1);
  const markets = await (await call(url, '/markets', { cookie: alice })).json();
  assert.equal(markets.items.length, 11);
  assert.ok(markets.items.every((m) => m.price === null && m.change_pct === null));
});

test('proxy owns identity and streams while durably archiving messages for only their owner', async (context) => {
  const received = [];
  let completed = false;
  let finish;
  const completion = new Promise((resolve) => {
    finish = resolve;
  });
  const engine = await listen(
    createServer(async (req, res) => {
      let body = '';
      for await (const chunk of req) body += chunk;
      received.push({
        path: req.url,
        user: req.headers['x-user-id'],
        authorization: req.headers.authorization,
        body: body ? JSON.parse(body) : null,
      });
      if (req.url === '/v1/web/preferences') {
        res.end('{"horizon":"position","risk_level":"low"}');
        return;
      }
      if (req.url === '/v1/chat') {
        res.writeHead(200, { 'Content-Type': 'application/x-ndjson' });
        res.write('{"type":"text","content":"Türkçe"}\n');
        void completion.then(() => {
          completed = true;
          res.end(
            '{"type":"text","content":" yanıt"}\n{"type":"recommendation","recommendation_id":"33333333-3333-4333-8333-333333333333"}\n',
          );
        });
        return;
      }
      res.end('{}');
    }),
    context,
  );
  const url = await listen(
    createUserUIServer({ engineUrl: engine, engineKey: 'private-key' }),
    context,
  );
  const cookie = await register(url),
    bob = await register(url, 'trader_two');
  const response = await call(url, '/chat', {
    cookie,
    method: 'POST',
    body: {
      session_id: 'stolen',
      user_id: 'someone-else',
      message: 'test',
      preferences: { risk_level: 'high' },
    },
  });
  const reader = response.body.getReader();
  const first = await reader.read();
  assert.equal(completed, false);
  assert.match(new TextDecoder().decode(first.value), /Türkçe/);
  finish();
  while (!(await reader.read()).done) {
    /* consume stream */
  }
  const history = await (await call(url, '/history', { cookie })).json();
  assert.equal(history.messages.length, 2);
  assert.equal(history.messages[1].text, 'Türkçe yanıt');
  assert.deepEqual(history.messages[1].recommendationIds, ['33333333-3333-4333-8333-333333333333']);
  assert.equal(
    (await call(url, '/recommendations', { cookie, method: 'POST', body: { producer: 'model' } }))
      .status,
    404,
  );
  assert.deepEqual(history.messages[0].preferences, { horizon: 'position', risk_level: 'low' });
  assert.deepEqual((await (await call(url, '/history', { cookie: bob })).json()).messages, []);
  const proxied = received.find((r) => r.path === '/v1/chat');
  assert.equal(proxied.authorization, 'Bearer private-key');
  assert.equal(proxied.body.session_id, history.conversationId);
  assert.equal(proxied.user, (await (await call(url, '/session', { cookie })).json()).user.id);
  assert.equal(response.headers.get('authorization'), null);
  for (const path of ['/debug/state', '/jobs/daily-digest'])
    assert.equal((await call(url, path, { cookie })).status, 404);
});

test('watchlist, trading preferences and portfolio calls pass the same authenticated identity', async (context) => {
  const received = [];
  const engine = await listen(
    createServer(async (req, res) => {
      let body = '';
      for await (const chunk of req) body += chunk;
      received.push({ method: req.method, path: req.url, user: req.headers['x-user-id'], body });
      res.end('{}');
    }),
    context,
  );
  const url = await listen(createUserUIServer({ engineUrl: engine, engineKey: 'key' }), context),
    cookie = await register(url);
  for (const [path, method, body] of [
    ['/watchlist', 'GET'],
    ['/watchlist', 'POST', { symbol: 'THYAO' }],
    ['/watchlist/THYAO', 'DELETE'],
    ['/preferences', 'PUT', { horizon: 'swing', risk_level: 'high' }],
    ['/portfolio', 'GET'],
    ['/recommendations', 'GET'],
    [
      '/recommendations/33333333-3333-4333-8333-333333333333/accept',
      'POST',
      { quantity: 10, price: 100, operation_id: 'test' },
    ],
    ['/portfolio/performance?source=system', 'GET'],
  ])
    assert.equal((await call(url, path, { cookie, method, body })).status, 200);
  assert.equal(new Set(received.map((r) => r.user)).size, 1);
  assert.equal(received[3].path, '/v1/web/preferences');
});

test('cross origin writes, large bodies, weak passwords and login guessing are rejected', async (context) => {
  const url = await listen(createUserUIServer({ engineUrl: 'http://127.0.0.1:1' }), context);
  assert.equal(
    (await call(url, '/login', { method: 'POST', origin: 'https://other.example', body: {} }))
      .status,
    403,
  );
  assert.equal((await fetch(url + '/api/login', { method: 'POST', body: '{}' })).status, 403);
  assert.equal(
    (
      await call(url, '/register', {
        method: 'POST',
        body: { username: 'valid_name', password: 'short' },
      })
    ).status,
    400,
  );
  assert.equal(
    (await call(url, '/login', { method: 'POST', body: { password: 'x'.repeat(70000) } })).status,
    413,
  );
  for (let i = 0; i < 8; i++)
    await call(url, '/login', { method: 'POST', body: { username: 'nobody', password: 'wrong' } });
  assert.equal(
    (await call(url, '/login', { method: 'POST', body: { username: 'nobody', password: 'wrong' } }))
      .status,
    429,
  );
});

test('upstream failure is visible and missing assets do not return SPA HTML', async (context) => {
  const directory = await mkdtemp(join(tmpdir(), 'trader-web-'));
  await writeFile(join(directory, 'index.html'), '<html>Trader Agent</html>');
  context.after(() => rm(directory, { recursive: true }));
  const url = await listen(
      createUserUIServer({ engineUrl: 'http://127.0.0.1:1', staticRoot: directory }),
      context,
    ),
    cookie = await register(url);
  assert.equal((await call(url, '/meta', { cookie })).status, 502);
  assert.equal((await fetch(url + '/assets/missing.js')).status, 404);
  assert.match(await (await fetch(url + '/portfolio')).text(), /Trader Agent/);
});
