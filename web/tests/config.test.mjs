import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadWebConfig } from '../config.mjs';
import { resolveConfig } from 'vite';
import { fileURLToPath } from 'node:url';

const env = {
  WEB_HOST: '127.0.0.1',
  WEB_PORT: '9124',
  ENGINE_URL: 'http://127.0.0.1:9123',
};

test('web settings preserve explicit listener and engine ports', () => {
  const config = loadWebConfig({ ...env, ENGINE_API_KEY: 'private-key' });
  assert.equal(config.host, '127.0.0.1');
  assert.equal(config.port, 9124);
  assert.equal(config.engineUrl, 'http://127.0.0.1:9123');
  assert.equal(config.engineKey, 'private-key');
});

for (const name of ['WEB_HOST', 'WEB_PORT', 'ENGINE_URL']) {
  test(`web requires ${name} instead of using a hardcoded address`, () => {
    assert.throws(() => loadWebConfig({ ...env, [name]: '' }), new RegExp(name));
  });
}

for (const port of ['0', '-1', '65536', 'invalid']) {
  test(`invalid web port ${port} is rejected`, () => {
    assert.throws(() => loadWebConfig({ ...env, WEB_PORT: port }), /WEB_PORT/);
  });
}

test('Vite proxy follows the configured web listener port', async (context) => {
  const previous = { WEB_HOST: process.env.WEB_HOST, WEB_PORT: process.env.WEB_PORT };
  context.after(() => {
    for (const [name, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  });
  process.env.WEB_HOST = '127.0.0.1';
  process.env.WEB_PORT = '9124';
  const config = await resolveConfig(
    {
      root: fileURLToPath(new URL('../', import.meta.url)),
      configFile: fileURLToPath(new URL('../vite.config.ts', import.meta.url)),
    },
    'serve',
  );
  assert.equal(config.server.proxy['/api'], 'http://127.0.0.1:9124');
  assert.equal(config.server.proxy['/health'], 'http://127.0.0.1:9124');
});
