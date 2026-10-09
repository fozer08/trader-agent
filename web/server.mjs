import { createServer, request as httpRequest } from 'node:http';
import { request as httpsRequest } from 'node:https';
import { createHmac, randomBytes, timingSafeEqual, createHash } from 'node:crypto';
import { readFile, stat } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadWebConfig } from './config.mjs';

const root = fileURLToPath(new URL('./dist/', import.meta.url));
const loopbacks = new Set(['localhost', '127.0.0.1', '::1', '[::1]']);
const sessionLifetime = 12 * 60 * 60 * 1000;
const apiRoutes = [
  ['GET', /^\/meta$/, '/v1/web/meta'],
  ['GET', /^\/stocklist$/, '/v1/web/stocklist'],
  ['GET', /^\/watchlist$/, '/v1/web/watchlist'],
  ['POST', /^\/watchlist$/, '/v1/web/watchlist'],
  ['DELETE', /^\/watchlist\/[A-Za-z0-9]+$/, null],
  ['GET', /^\/symbols\/[A-Za-z0-9]+(?:\/bars)?$/, null],
  ['GET', /^\/portfolio(?:\/closed|\/[A-Za-z0-9]+\/transactions)?$/, null],
  ['GET', /^\/news$/, '/v1/web/news'],
  ['POST', /^\/portfolio\/transactions$/, '/v1/web/portfolio/transactions'],
  ['PATCH', /^\/portfolio\/[A-Za-z0-9]+$/, null],
  ['POST', /^\/chat$/, '/v1/chat'],
  ['POST', /^\/reset$/, '/v1/reset'],
];

function constantEqual(a, b) {
  return timingSafeEqual(
    createHash('sha256').update(a).digest(),
    createHash('sha256').update(b).digest(),
  );
}

export function createWebServer({
  engineUrl,
  engineKey = '',
  password = '',
  origin = '',
  secret = randomBytes(32).toString('hex'),
  staticRoot = root,
} = {}) {
  staticRoot = resolve(staticRoot);
  const upstream = new URL(engineUrl);
  if (!['http:', 'https:'].includes(upstream.protocol))
    throw new Error('ENGINE_URL HTTP(S) olmalı.');
  const allowedOrigin = origin ? new URL(origin).origin : '';
  const attempts = new Map();
  const sessions = new Map();
  const sign = (data) => createHmac('sha256', secret).update(data).digest('base64url');
  const cookie = (value, secure, maxAge = sessionLifetime / 1000) =>
    `trader_session=${value}; HttpOnly; SameSite=Strict; Path=/; Max-Age=${maxAge}${secure ? '; Secure' : ''}`;
  const send = (response, status, body, headers = {}) => {
    response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', ...headers });
    response.end(JSON.stringify(body));
  };
  function readSession(request) {
    const value = (request.headers.cookie || '')
      .split(';')
      .map((part) => part.trim())
      .find((part) => part.startsWith('trader_session='))
      ?.slice(15);
    if (!value) return null;
    const [data, signature] = value.split('.');
    if (!data || !signature || !constantEqual(signature, sign(data))) return null;
    try {
      const session = JSON.parse(Buffer.from(data, 'base64url').toString());
      return session.expires > Date.now() && sessions.get(session.id) === session.expires
        ? session
        : null;
    } catch {
      return null;
    }
  }
  const authorized = (request) => !password || !!readSession(request);
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
  return createServer(async (request, response) => {
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
        send(response, 200, { status: 'ok' });
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
          (password && !requestedOrigin) ||
          (requestedOrigin && requestedOrigin !== expectedOrigin && !devOrigin))
      ) {
        send(response, 403, { detail: 'İstek kaynağı doğrulanamadı.' });
        return;
      }
      const secure = allowedOrigin.startsWith('https:');
      if (url.pathname === '/api/session' && request.method === 'GET') {
        send(response, 200, { authenticated: authorized(request), login_required: !!password });
        return;
      }
      if (url.pathname === '/api/login' && request.method === 'POST') {
        const key = request.socket.remoteAddress;
        const now = Date.now();
        for (const [ip, record] of attempts) if (now > record.until) attempts.delete(ip);
        const record = attempts.get(key) || { count: 0, until: now + 15 * 60 * 1000 };
        if (record.count >= 10) {
          send(response, 429, { detail: 'Çok fazla deneme. 15 dakika sonra tekrar deneyin.' });
          return;
        }
        const input = JSON.parse((await body(request)).toString());
        if (typeof input.password !== 'string' || !constantEqual(input.password, password)) {
          record.count++;
          attempts.set(key, record);
          send(response, 401, { detail: 'Parola doğru değil.' });
          return;
        }
        attempts.delete(key);
        for (const [id, expires] of sessions) if (expires <= now) sessions.delete(id);
        const session = { expires: now + sessionLifetime, id: randomBytes(16).toString('hex') };
        sessions.set(session.id, session.expires);
        const data = Buffer.from(JSON.stringify(session)).toString('base64url');
        send(
          response,
          200,
          { ok: true },
          { 'Set-Cookie': cookie(`${data}.${sign(data)}`, secure) },
        );
        return;
      }
      if (url.pathname === '/api/logout' && request.method === 'POST') {
        const session = readSession(request);
        if (session) sessions.delete(session.id);
        send(response, 200, { ok: true }, { 'Set-Cookie': cookie('', secure, 0) });
        return;
      }
      if (url.pathname.startsWith('/api/')) {
        if (!authorized(request)) {
          send(response, 401, { detail: 'Oturum açmanız gerekiyor.' });
          return;
        }
        const path = url.pathname.slice(4);
        const route = apiRoutes.find(
          ([method, pattern]) => method === request.method && pattern.test(path),
        );
        if (!route) {
          send(response, 404, { detail: 'Bu API yolu bulunamadı.' });
          return;
        }
        const target = new URL(route[2] || `/v1/web${path}`, upstream);
        target.search = url.search;
        const payload = ['POST', 'PATCH'].includes(request.method)
          ? await body(request)
          : undefined;
        if (path === '/chat' || path === '/reset') {
          const input = JSON.parse(payload.toString());
          if (!/^web:[a-zA-Z0-9-]{1,100}$/.test(input.session_id || '')) {
            send(response, 400, { detail: 'Geçersiz web sohbeti.' });
            return;
          }
        }
        const proxy = (target.protocol === 'https:' ? httpsRequest : httpRequest)(
          target,
          {
            method: request.method,
            headers: {
              Accept: 'application/json, application/x-ndjson',
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
            incoming.on('error', () => response.destroy());
            incoming.pipe(response);
          },
        );
        proxy.setTimeout(600000, () => proxy.destroy(new Error('Engine timeout')));
        proxy.on('error', () => {
          if (!response.headersSent)
            send(response, 502, { detail: 'Engine bağlantısı kurulamadı.' });
          else response.destroy();
        });
        response.on('close', () => proxy.destroy());
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
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const config = loadWebConfig();
  const { host, port } = config;
  if (!loopbacks.has(host) && !config.password) {
    throw new Error('Dış ağ erişimi için WEB_PASSWORD tanımlayın.');
  }
  const server = createWebServer(config);
  server.listen(port, host, () =>
    process.stdout.write(`Trader Agent web: http://${host}:${port}\n`),
  );
  for (const signal of ['SIGTERM', 'SIGINT'])
    process.on(signal, () => server.close(() => process.exit(0)));
}
