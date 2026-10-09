export function loadWebConfig(env = process.env) {
  const host = env.WEB_HOST;
  if (!host) throw new Error('WEB_HOST zorunlu.');
  const port = Number(env.WEB_PORT);
  if (!Number.isInteger(port) || port < 1 || port > 65535)
    throw new Error('WEB_PORT, 1–65535 arasında bir tam sayı olmalı.');
  const engineUrl = env.ENGINE_URL;
  if (!engineUrl) throw new Error('ENGINE_URL zorunlu.');
  return {
    host,
    port,
    engineUrl,
    engineKey: env.ENGINE_API_KEY || '',
    password: env.WEB_PASSWORD || '',
    origin: env.WEB_ORIGIN || '',
  };
}
