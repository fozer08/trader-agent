import type { ConversationPreferences, Horizon, RiskLevel } from './types';

export const horizons: [Horizon, string][] = [
  ['intraday', 'Seans içi'],
  ['swing', 'Günler–haftalar'],
  ['position', '1–3 ay'],
];
export const risks: [RiskLevel, string][] = [
  ['low', 'Düşük'],
  ['moderate', 'Orta'],
  ['high', 'Yüksek'],
];
export const emptyPreferences: ConversationPreferences = { horizon: null, risk_level: null };

export function preferenceLabel(value: ConversationPreferences): string {
  const horizon = horizons.find(([key]) => key === value.horizon)?.[1] ?? 'Vade belirtilmedi';
  const risk = risks.find(([key]) => key === value.risk_level)?.[1];
  return `${horizon} · ${risk ? `${risk} risk` : 'Risk belirtilmedi'}`;
}
