import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parseEnv } from 'node:util';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ command }) => {
  const plugins = [react()];
  if (command === 'build') return { plugins };
  const envFile = fileURLToPath(new URL('../.env', import.meta.url));
  const env = {
    ...(existsSync(envFile) ? parseEnv(readFileSync(envFile, 'utf8')) : {}),
    ...process.env,
  };
  if (!env.USERUI_HOST || !env.USERUI_PORT) throw new Error('USERUI_HOST ve USERUI_PORT zorunlu.');
  const host = env.USERUI_HOST === '0.0.0.0' ? '127.0.0.1' : env.USERUI_HOST;
  const target = `http://${host.includes(':') ? `[${host}]` : host}:${env.USERUI_PORT}`;
  return { plugins, server: { proxy: { '/api': target, '/health': target } } };
});
