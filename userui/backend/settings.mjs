export const marketInstruments = [
  ['bist100', 'BIST 100'],
  ['bist30', 'BIST 20'],
  ['usdtry', 'USD / TL'],
  ['eurtry', 'EUR / TL'],
  ['brent', 'Brent petrol'],
  ['gold', 'Altın (ons)'],
  ['bitcoin', 'Bitcoin'],
  ['us10y', 'ABD 10Y'],
  ['us2y', 'ABD 2Y'],
  ['tr10y', 'Türkiye 10Y'],
  ['tr2y', 'Türkiye 2Y'],
];
export function validateSettings(input) {
  const allowed = ['sidebarCollapsed', 'chatWidth', 'markets', 'portfolioTab'];
  if (
    !input ||
    typeof input !== 'object' ||
    Object.keys(input).some((key) => !allowed.includes(key)) ||
    (input.portfolioTab !== undefined &&
      !['recommended', 'open', 'closed', 'performance'].includes(input.portfolioTab)) ||
    typeof input.sidebarCollapsed !== 'boolean' ||
    !Number.isInteger(input.chatWidth) ||
    input.chatWidth < 340 ||
    input.chatWidth > 960 ||
    !Array.isArray(input.markets) ||
    input.markets.length > marketInstruments.length ||
    new Set(input.markets).size !== input.markets.length ||
    input.markets.some((key) => !marketInstruments.some(([id]) => id === key))
  )
    throw Object.assign(new Error('Ekran tercihlerini kontrol edin.'), { status: 400 });
  return input;
}
