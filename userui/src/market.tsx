import { createContext, useContext } from 'react';
import type { Metadata } from './types';
import { date, time } from './format';

export const MarketContext = createContext<Metadata | undefined>(undefined);
export const useMarket = () => useContext(MarketContext);
export function useMarketFormat() {
  const timezone = useMarket()?.timezone ?? 'Europe/Istanbul';
  return {
    date: (value: string | null | undefined, withTime = false) => date(value, withTime, timezone),
    time: (value: string | null | undefined) => time(value, timezone),
  };
}

export function sessionLabel(meta: Metadata | undefined): string {
  if (!meta) return 'Bağlanıyor';
  if (meta.day_kind === 'unknown') return 'Takvim kapsamı dışında';
  if (meta.day_kind === 'closed') return `Seans kapalı · ${meta.reason ?? 'Tatil'}`;
  if (meta.day_kind === 'half_day') {
    return `Yarım gün · ${meta.session_start}–${meta.session_end} · ${meta.scheduled_open ? 'Açık' : 'Kapalı'}`;
  }
  return meta.scheduled_open ? 'Seans saatleri içinde' : 'Seans saatleri dışında';
}
