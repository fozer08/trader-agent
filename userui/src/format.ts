export const number = (value: number | null | undefined, digits = 2) =>
  value == null || !Number.isFinite(value)
    ? '—'
    : new Intl.NumberFormat('tr-TR', {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      }).format(value);
export const money = (value: number | null | undefined) =>
  value == null ? '—' : `${number(value)} ₺`;
export const percent = (value: number | null | undefined) =>
  value == null ? '—' : `${value > 0 ? '+' : ''}${number(value)}%`;
export const tone = (value: number | null | undefined) =>
  value == null || value === 0 ? '' : value > 0 ? 'positive' : 'negative';
export const date = (
  value: string | null | undefined,
  withTime = false,
  timezone = 'Europe/Istanbul',
) =>
  !value
    ? '—'
    : new Intl.DateTimeFormat('tr-TR', {
        timeZone: /^\d{4}-\d{2}-\d{2}$/.test(value) ? 'UTC' : timezone,
        day: '2-digit',
        month: 'short',
        year: 'numeric',
        ...(withTime ? ({ hour: '2-digit', minute: '2-digit' } as const) : {}),
      }).format(new Date(value));
export const time = (value: string | null | undefined, timezone = 'Europe/Istanbul') =>
  !value
    ? '—'
    : new Intl.DateTimeFormat('tr-TR', {
        timeZone: timezone,
        hour: '2-digit',
        minute: '2-digit',
      }).format(new Date(value));
export const priceLabel = (kind: 'delayed' | 'close' | 'unavailable' | undefined) =>
  kind === 'close' ? 'Son kapanış' : kind === 'delayed' ? 'Gecikmeli fiyat' : 'Fiyat bulunamadı';
export const trend = (value: string | null | undefined) =>
  value === 'above'
    ? 'EMA üstünde'
    : value === 'below'
      ? 'EMA altında'
      : value === 'equal'
        ? 'EMA seviyesinde'
        : '—';
