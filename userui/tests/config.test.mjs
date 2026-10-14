import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadUserUIConfig } from '../config.mjs';
import { resolveConfig } from 'vite';
import { fileURLToPath } from 'node:url';

const env = {
  ENGINE_API_KEY: 'test-key',
  USERUI_HOST: '127.0.0.1',
  USERUI_PORT: '9124',
  ENGINE_URL: 'http://127.0.0.1:9123',
};

test('web settings preserve explicit listener and engine ports', () => {
  const config = loadUserUIConfig({ ...env, ENGINE_API_KEY: 'private-key' });
  assert.equal(config.host, '127.0.0.1');
  assert.equal(config.port, 9124);
  assert.equal(config.engineUrl, 'http://127.0.0.1:9123');
  assert.equal(config.engineKey, 'private-key');
});

for (const name of ['USERUI_HOST', 'USERUI_PORT', 'ENGINE_URL', 'ENGINE_API_KEY']) {
  test(`web requires ${name} instead of using a hardcoded address`, () => {
    assert.throws(() => loadUserUIConfig({ ...env, [name]: '' }), new RegExp(name));
  });
}

for (const port of ['0', '-1', '65536', 'invalid']) {
  test(`invalid web port ${port} is rejected`, () => {
    assert.throws(() => loadUserUIConfig({ ...env, USERUI_PORT: port }), /USERUI_PORT/);
  });
}

test('Vite proxy follows the configured web listener port', async (context) => {
  const previous = { USERUI_HOST: process.env.USERUI_HOST, USERUI_PORT: process.env.USERUI_PORT };
  context.after(() => {
    for (const [name, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  });
  process.env.USERUI_HOST = '127.0.0.1';
  process.env.USERUI_PORT = '9124';
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
