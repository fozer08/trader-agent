export function loadUserUIConfig(env = process.env) {
  const host = env.USERUI_HOST;
  if (!host) throw new Error('USERUI_HOST zorunlu.');
  const port = Number(env.USERUI_PORT);
  if (!Number.isInteger(port) || port < 1 || port > 65535)
    throw new Error('USERUI_PORT, 1–65535 arasında bir tam sayı olmalı.');
  const engineUrl = env.ENGINE_URL;
  if (!engineUrl) throw new Error('ENGINE_URL zorunlu.');
  if (!env.ENGINE_API_KEY) throw new Error('ENGINE_API_KEY zorunlu.');
  return {
    host,
    port,
    engineUrl,
    engineKey: env.ENGINE_API_KEY || '',
    databasePath: env.USERUI_DATABASE_PATH || new URL('../data/web.db', import.meta.url).pathname,
    origin: env.USERUI_ORIGIN || '',
  };
}
