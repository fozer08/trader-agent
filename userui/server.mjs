import { createServer, request as httpRequest } from 'node:http';
import { request as httpsRequest } from 'node:https';
import { UserStore } from './backend/store.mjs';
import { validateSettings, marketInstruments } from './backend/settings.mjs';
import { readFile, stat } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadUserUIConfig } from './config.mjs';

const root = fileURLToPath(new URL('./dist/', import.meta.url));
const loopbacks = new Set(['localhost', '127.0.0.1', '::1', '[::1]']);
const sessionLifetime = 12 * 60 * 60 * 1000;
const apiRoutes = [
  ['GET', /^\/preferences$/, '/v1/web/preferences'],
  ['PUT', /^\/preferences$/, '/v1/web/preferences'],
  ['GET', /^\/meta$/, '/v1/web/meta'],
  ['GET', /^\/stocklist$/, '/v1/web/stocklist'],
  ['GET', /^\/watchlist$/, '/v1/web/watchlist'],
  ['POST', /^\/watchlist$/, '/v1/web/watchlist'],
  ['DELETE', /^\/watchlist\/[A-Za-z0-9]+$/, null],
  ['GET', /^\/symbols\/[A-Za-z0-9]+(?:\/bars)?$/, null],
  ['GET', /^\/portfolio(?:\/(?:closed|performance)|\/[A-Za-z0-9-]+(?:\/transactions)?)?$/, null],
  ['GET', /^\/recommendations(?:\/[A-Za-z0-9-]+)?$/, null],
  ['POST', /^\/recommendations\/[A-Za-z0-9-]+\/accept$/, null],
  ['GET', /^\/news$/, '/v1/web/news'],
  ['POST', /^\/portfolio\/preview$/, '/v1/web/portfolio/preview'],
  ['POST', /^\/portfolio\/transactions$/, '/v1/web/portfolio/transactions'],
  ['PATCH', /^\/portfolio\/[A-Za-z0-9-]+$/, null],
  ['POST', /^\/chat$/, '/v1/chat'],
  ['POST', /^\/reset$/, '/v1/reset'],
];

function objectInput(buffer) {
  const input = JSON.parse(buffer.toString());
  if (!input || typeof input !== 'object' || Array.isArray(input))
    throw Object.assign(new Error('Geçersiz istek.'), { status: 400 });
  return input;
}

export function createUserUIServer({
  engineUrl,
  engineKey = '',
  databasePath = ':memory:',
  origin = '',
  staticRoot = root,
} = {}) {
  staticRoot = resolve(staticRoot);
  const upstream = new URL(engineUrl);
  if (!['http:', 'https:'].includes(upstream.protocol))
    throw new Error('ENGINE_URL HTTP(S) olmalı.');
  const allowedOrigin = origin ? new URL(origin).origin : '';
  const attempts = new Map();
  const store = new UserStore(databasePath);
  const activeChats = new Set();
  const cookie = (value, secure, maxAge = sessionLifetime / 1000) =>
    `trader_session=${value}; HttpOnly; SameSite=Strict; Path=/; Max-Age=${maxAge}${secure ? '; Secure' : ''}`;
  const send = (response, status, body, headers = {}) => {
    response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', ...headers });
    response.end(JSON.stringify(body));
  };
  function sessionToken(request) {
    return (
      (request.headers.cookie || '')
        .split(';')
        .map((p) => p.trim())
        .find((p) => p.startsWith('trader_session='))
        ?.slice(15) || ''
    );
  }
  async function body(request) {
    const chunks = [];
    let size = 0;
    for await (const chunk of request) {
      size += chunk.length;
      if (size > 65536) {
        const error = new Error('İstek çok büyük.');
        error.status = 413;
        throw error;
      }
      chunks.push(chunk);
    }
    return Buffer.concat(chunks);
  }
  const server = createServer(async (request, response) => {
    response.setHeader('X-Content-Type-Options', 'nosniff');
    response.setHeader('Referrer-Policy', 'same-origin');
    response.setHeader('X-Frame-Options', 'DENY');
    response.setHeader('Cache-Control', 'no-store');
    response.setHeader(
      'Content-Security-Policy',
      "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    );
    try {
      const url = new URL(request.url, 'http://localhost');
      if (url.pathname === '/health' && request.method === 'GET') {
        send(response, 200, await store.call('ping'));
        return;
      }
      const requestedOrigin = request.headers.origin;
      const host = new URL(`http://${request.headers.host || 'localhost'}`);
      // Fixed cloud origin, or loopback hosts for local development. Never reflect arbitrary hosts.
      const expectedOrigin = allowedOrigin || (loopbacks.has(host.hostname) ? host.origin : '');
      const devOrigin =
        !allowedOrigin &&
        loopbacks.has(host.hostname) &&
        ['http://localhost:5173', 'http://127.0.0.1:5173'].includes(requestedOrigin);
      if (
        !['GET', 'HEAD'].includes(request.method) &&
        (!expectedOrigin ||
          !requestedOrigin ||
          (requestedOrigin && requestedOrigin !== expectedOrigin && !devOrigin))
      ) {
        send(response, 403, { detail: 'İstek kaynağı doğrulanamadı.' });
        return;
      }
      const secure = allowedOrigin.startsWith('https:');
      const user = url.pathname.startsWith('/api/')
        ? await store.call('session', { token: sessionToken(request) })
        : null;
      if (url.pathname === '/api/session' && request.method === 'GET') {
        send(response, 200, { authenticated: !!user, login_required: true, user });
        return;
      }
      if (['/api/login', '/api/register'].includes(url.pathname) && request.method === 'POST') {
        const key = request.socket.remoteAddress,
          now = Date.now();
        for (const [ip, record] of attempts) if (now > record.until) attempts.delete(ip);
        const record = attempts.get(key) || { count: 0, until: now + 900000 };
        if (record.count >= 10) {
          send(response, 429, { detail: 'Çok fazla deneme. 15 dakika sonra tekrar deneyin.' });
          return;
        }
        record.count++;
        attempts.set(key, record);
        const input = objectInput(await body(request));
        if (!input || typeof input !== 'object' || Array.isArray(input)) {
          send(response, 400, { detail: 'Kullanıcı adı ve parola gerekli.' });
          return;
        }
        const account = await store.call(
          url.pathname.endsWith('register') ? 'register' : 'login',
          input,
        );
        const token = await store.call('issue', { userId: account.id });
        send(response, 200, { ok: true }, { 'Set-Cookie': cookie(token, secure) });
        return;
      }
      if (url.pathname === '/api/logout' && request.method === 'POST') {
        await store.call('logout', { token: sessionToken(request) });
        send(response, 200, { ok: true }, { 'Set-Cookie': cookie('', secure, 0) });
        return;
      }
      if (url.pathname.startsWith('/api/')) {
        if (!user) {
          send(response, 401, { detail: 'Oturum açmanız gerekiyor.' });
          return;
        }
        const path = url.pathname.slice(4);
        const userId = user.id;
        if (path === '/settings' && ['GET', 'PUT'].includes(request.method)) {
          const value =
            request.method === 'PUT'
              ? await store.call('saveSettings', {
                  userId,
                  value: validateSettings(objectInput(await body(request))),
                })
              : await store.call('settings', { userId });
          send(response, 200, {
            sidebarCollapsed: false,
            chatWidth: 420,
            markets: ['bist100', 'usdtry', 'gold'],
            ...value,
          });
          return;
        }
        if (path === '/markets' && request.method === 'GET') {
          send(response, 200, {
            items: marketInstruments.map(([id, name]) => ({
              id,
              name,
              price: null,
              change_pct: null,
              as_of: null,
            })),
          });
          return;
        }
        if (path === '/history' && request.method === 'GET') {
          send(response, 200, await store.call('history', { userId }));
          return;
        }
        if (path === '/conversations' && request.method === 'GET') {
          send(response, 200, { items: await store.call('conversations', { userId }) });
          return;
        }
        if (path === '/conversation' && request.method === 'POST') {
          if (activeChats.has(userId)) {
            send(response, 409, { detail: 'Önce devam eden yanıtı durdurun.' });
            return;
          }
          const input = objectInput(await body(request));
          send(response, 200, await store.call('selectChat', { userId, id: input.id }));
          return;
        }
        if (path === '/reset' && request.method === 'POST') {
          if (activeChats.has(userId)) {
            send(response, 409, { detail: 'Önce devam eden yanıtı durdurun.' });
            return;
          }
          send(response, 200, await store.call('newChat', { userId }));
          return;
        }

        const route = apiRoutes.find(
          ([method, pattern]) => method === request.method && pattern.test(path),
        );
        if (!route) {
          send(response, 404, { detail: 'Bu API yolu bulunamadı.' });
          return;
        }
        const target = new URL(route[2] || `/v1/web${path}`, upstream);
        target.search = url.search;
        let payload = ['POST', 'PATCH', 'PUT'].includes(request.method)
          ? await body(request)
          : undefined;
        let chat;
        if (path === '/chat') {
          const input = objectInput(payload);
          if (
            typeof input.message !== 'string' ||
            !input.message.trim() ||
            input.message.length > 16000
          ) {
            send(response, 400, { detail: 'Mesaj 1–16000 karakter olmalı.' });
            return;
          }
          if (activeChats.has(userId)) {
            send(response, 409, { detail: 'Bir yanıt zaten hazırlanıyor.' });
            return;
          }
          activeChats.add(userId);
          try {
            const prefResponse = await fetch(new URL('/v1/web/preferences', upstream), {
              headers: { Authorization: `Bearer ${engineKey}`, 'X-User-Id': userId },
              signal: AbortSignal.timeout(10000),
            });
            if (!prefResponse.ok) throw new Error('Tercihler alınamadı.');
            const preferences = await prefResponse.json();
            if (
              !preferences ||
              ![null, 'intraday', 'swing', 'position'].includes(preferences.horizon) ||
              ![null, 'low', 'moderate', 'high'].includes(preferences.risk_level)
            )
              throw new Error('Tercihler doğrulanamadı.');
            chat = await store.call('startMessage', { userId, text: input.message, preferences });
            payload = Buffer.from(
              JSON.stringify({
                message: input.message,
                session_id: chat.conversationId,
                output_format: 'markdown',
                preferences,
              }),
            );
          } catch (error) {
            activeChats.delete(userId);
            throw error;
          }
        }
        const recommendationIds = new Set();
        let answer = '',
          streamError = '',
          buffer = '',
          finished = false;
        async function finishChat(error) {
          if (!chat || finished) return;
          finished = true;
          try {
            await store.call('finishMessage', {
              conversationId: chat.conversationId,
              text: answer,
              error: error || streamError,
              recommendationIds: [...recommendationIds],
            });
          } catch {
            if (!response.destroyed) response.destroy(new Error('Sohbet kaydedilemedi.'));
          } finally {
            activeChats.delete(userId);
          }
        }
        const proxy = (target.protocol === 'https:' ? httpsRequest : httpRequest)(
          target,
          {
            method: request.method,
            headers: {
              Accept: 'application/json, application/x-ndjson',
              'X-User-Id': userId,
              ...(payload
                ? { 'Content-Type': 'application/json', 'Content-Length': payload.length }
                : {}),
              ...(engineKey ? { Authorization: `Bearer ${engineKey}` } : {}),
            },
          },
          (incoming) => {
            response.writeHead(incoming.statusCode || 502, {
              'Content-Type': incoming.headers['content-type'] || 'application/json',
              'X-Accel-Buffering': 'no',
            });
            if (chat) {
              incoming.setEncoding('utf8');
              incoming.on('data', (chunk) => {
                if (answer.length + buffer.length + chunk.length > 2000000) {
                  streamError = 'Yanıt boyutu sınırı aşıldı.';
                  incoming.destroy();
                  return;
                }
                buffer += chunk;
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';
                for (const line of lines) {
                  if (!line.trim()) continue;
                  try {
                    const event = JSON.parse(line);
                    if (event.type === 'text' && typeof event.content === 'string')
                      answer += event.content;
                    if (
                      event.type === 'recommendation' &&
                      typeof event.recommendation_id === 'string' &&
                      /^[a-f0-9-]{36}$/.test(event.recommendation_id) &&
                      recommendationIds.size < 50
                    )
                      recommendationIds.add(event.recommendation_id);
                    if (event.type === 'error') streamError = 'Yanıt tamamlanamadı.';
                  } catch {
                    streamError = 'Yanıt tamamlanamadı.';
                  }
                }
              });
              incoming.on('end', async () => {
                await finishChat(
                  incoming.statusCode >= 400
                    ? 'Yanıt alınamadı.'
                    : buffer.trim()
                      ? 'Yanıt eksik kaldı.'
                      : undefined,
                );
                response.end();
              });
            }
            incoming.on('error', () => {
              void finishChat('Yanıt kesildi.');
              response.destroy();
            });
            incoming.pipe(response, { end: !chat });
          },
        );
        proxy.setTimeout(600000, () => proxy.destroy(new Error('Engine timeout')));
        proxy.on('error', () => {
          void finishChat('Yanıt alınamadı.');
          if (!response.headersSent)
            send(response, 502, { detail: 'Hizmete şu anda ulaşılamıyor.' });
          else response.destroy();
        });
        response.on('close', () => {
          proxy.destroy();
          void finishChat('Yanıt durduruldu.');
        });
        proxy.end(payload);
        return;
      }
      if (!['GET', 'HEAD'].includes(request.method)) {
        send(response, 405, { detail: 'Geçersiz yöntem.' });
        return;
      }
      const decoded = decodeURIComponent(url.pathname);
      const file = resolve(staticRoot, `.${decoded}`);
      if (file !== staticRoot && !file.startsWith(`${resolve(staticRoot)}${sep}`)) {
        send(response, 404, { detail: 'Dosya bulunamadı.' });
        return;
      }
      const exists = await stat(file)
        .then((info) => info.isFile())
        .catch(() => false);
      if (!exists && extname(decoded)) {
        send(response, 404, { detail: 'Dosya bulunamadı.' });
        return;
      }
      const content = await readFile(exists ? file : resolve(staticRoot, 'index.html'));
      const mime = {
        '.html': 'text/html',
        '.js': 'text/javascript',
        '.css': 'text/css',
        '.svg': 'image/svg+xml',
        '.png': 'image/png',
        '.woff2': 'font/woff2',
      };
      response.setHeader(
        'Content-Type',
        `${mime[extname(exists ? file : 'index.html')] || 'application/octet-stream'}; charset=utf-8`,
      );
      if (decoded.startsWith('/assets/'))
        response.setHeader('Cache-Control', 'public, max-age=31536000, immutable');
      response.end(request.method === 'HEAD' ? undefined : content);
    } catch (error) {
      if (response.headersSent) response.destroy();
      else
        send(response, error.status || (error instanceof SyntaxError ? 400 : 500), {
          detail: error.status
            ? error.message
            : error instanceof SyntaxError
              ? 'Geçersiz JSON.'
              : 'Web isteği tamamlanamadı.',
        });
    }
  });
  server.on('close', () => {
    void store.close().catch(() => {
      process.stderr.write('Kullanıcı deposu kapatılamadı.\n');
    });
  });
  return server;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const config = loadUserUIConfig();
  const { host, port } = config;
  const server = createUserUIServer(config);
  server.listen(port, host, () =>
    process.stdout.write(`Trader Agent userui: http://${host}:${port}\n`),
  );
  for (const signal of ['SIGTERM', 'SIGINT'])
    process.on(signal, () => {
      server.close();
      const deadline = setTimeout(() => server.closeAllConnections(), 10000);
      deadline.unref();
    });
}
