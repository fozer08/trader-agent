import { test, expect, type Page } from '@playwright/test';
import type { Metadata } from '../../src/types';

const defaultMetadata: Metadata = {
  instance_id: 'engine-1',
  server_time: '2026-10-06T15:00:00+03:00',
  timezone: 'Europe/Istanbul',
  source: 'isyatirim',
  delay_minutes: 15,
  scheduled_open: true,
  session_date: '2026-10-06',
  day_kind: 'normal',
  reason: null,
  calendar_years: [2025, 2026, 2027],
  session_start: '10:00',
  session_end: '18:00',
  daily_close: '18:10',
  next_open: '2026-10-07T10:00:00+03:00',
  watchlist_count: 3,
};
const indicators = {
  ema_trend: 'above',
  ema_alignment: 'bullish',
  ema_fast: 95,
  ema_slow: 90,
  rsi: 56.4,
  rsi_divergence: null,
  macd_histogram: 0.45,
  atr: 3.5,
  bb_width: 0.1,
  bb_pct_b: 0.7,
  relative_volume: 1.2,
};
const quotes = ['THYAO', 'ASELS', 'BIMAS'].map((symbol, i) => ({
  symbol,
  name: ['Türk Hava Yolları', 'Aselsan', 'BİM'][i],
  pending: false,
  price: 100 + i * 20,
  change_pct: 2.5 - i,
  as_of: '2026-10-06T14:45:00+03:00',
  price_kind: 'delayed',
  daily: indicators,
}));

interface MockOptions {
  loginRequired?: boolean;
  initialPosition?: boolean;
  failSale?: boolean;
  failWatchlist?: boolean;
  metadataOverride?: Partial<Metadata>;
  quoteKind?: 'delayed' | 'close';
}

async function mockApi(
  page: Page,
  {
    loginRequired = false,
    initialPosition = false,
    failSale = false,
    failWatchlist = false,
    metadataOverride = {},
    quoteKind = 'delayed',
  }: MockOptions = {},
) {
  const metadata = { ...defaultMetadata, ...metadataOverride };
  const preparedQuotes = quotes.map((item) => ({ ...item, price_kind: quoteKind }));
  let authenticated = !loginRequired;
  let tracked = [...preparedQuotes];
  const stocks = [
    ...quotes.map(({ symbol, name }) => ({ symbol, name })),
    { symbol: 'TUPRS', name: 'Tüpraş' },
  ];
  const watchlistView = () => ({
    items: tracked,
    refreshing: false,
    updated_at: metadata.server_time,
  });
  let position: Record<string, unknown> | undefined = initialPosition
    ? {
        symbol: 'THYAO',
        quantity: 10,
        avg_cost: 90,
        current_price: 100,
        opened_at: '2026-10-01',
        hold_days: 6,
        realized_pnl: 0,
        unrealized_pnl: 100,
        unrealized_pnl_pct: 11.11,
        stop_loss: 85,
        target: 120,
        note: 'Test pozisyonu',
        distance_to_stop_pct: 15,
        distance_to_target_pct: 20,
        as_of: '2026-10-06T14:45:00+03:00',
        price_kind: 'delayed',
      }
    : undefined;
  const transactions: Record<string, unknown>[] = [];
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname.slice(4);
    const respond = (json: unknown, status = 200) => route.fulfill({ status, json });
    if (path === '/session') return respond({ authenticated, login_required: loginRequired });
    if (path === '/login') {
      if (route.request().postDataJSON().password !== 'correct')
        return respond({ detail: 'Parola doğru değil.' }, 401);
      authenticated = true;
      return respond({ ok: true });
    }
    if (path === '/logout') {
      authenticated = false;
      return respond({ ok: true });
    }
    if (path === '/meta') return respond({ ...metadata, watchlist_count: tracked.length });
    if (path === '/stocklist') return respond({ items: stocks });
    if (path === '/watchlist') {
      if (route.request().method() === 'POST') {
        if (failWatchlist) return respond({ detail: 'Takip listesi güncellenemedi.' }, 400);
        const stock = stocks.find((item) => item.symbol === route.request().postDataJSON().symbol);
        if (!stock) return respond({ detail: 'Hisse temel listede bulunamadı.' }, 400);
        if (!tracked.some((item) => item.symbol === stock.symbol))
          tracked.push({ ...quotes[0], ...stock });
      }
      return respond(watchlistView());
    }
    if (path.startsWith('/watchlist/') && route.request().method() === 'DELETE') {
      tracked = tracked.filter((item) => item.symbol !== path.split('/')[2]);
      return respond(watchlistView());
    }
    if (/\/symbols\/.+\/bars/.test(path)) {
      const timeframe = new URL(route.request().url()).searchParams.get('timeframe') ?? 'D1';
      return respond({
        symbol: 'THYAO',
        timeframe,
        chart_type: timeframe === 'D1' ? 'candlestick' : 'line',
        bars:
          timeframe === 'D1'
            ? Array.from({ length: 70 }, (_, i) => ({
                time: new Date(Date.UTC(2026, 6, 1 + i)).toISOString(),
                open: 90 + i / 5,
                high: 94 + i / 5,
                low: 89 + i / 5,
                close: 92 + i / 5,
                volume: 1000,
              }))
            : [],
      });
    }
    if (path.startsWith('/symbols/')) {
      const symbol = path.split('/')[2];
      return respond({
        ...preparedQuotes.find((item) => item.symbol === symbol),
        levels: {
          prev_high: 103,
          prev_low: 97,
          prev_close: 99,
          weekly_high: 106,
          weekly_low: 93,
          relative_volume: 1.2,
          pivot: { pp: 100, s1: 97, s2: 94, r1: 103, r2: 106 },
          candle: null,
        },
        intraday: { m5: null, m15: null },
      });
    }
    if (path === '/news')
      return respond({
        digests: [
          {
            date: '2026-10-06',
            sources: ['bloomberght'],
            item_count: 5,
            summary: 'Şirket haberleri ve piyasa gündemi için **günlük özet**.',
          },
        ],
      });
    if (path === '/portfolio')
      return respond({
        positions: position ? [position] : [],
        summary: {
          total_cost: position ? 900 : 0,
          market_value: position ? 1000 : 0,
          unrealized_pnl: position ? 100 : 0,
          unrealized_pnl_pct: position ? 11.11 : null,
          realized_pnl: 0,
          missing_prices: 0,
        },
      });
    if (path === '/portfolio/closed') return respond({ closed: [] });
    if (path.endsWith('/transactions') && route.request().method() === 'GET')
      return respond({ transactions });
    if (path === '/portfolio/transactions') {
      const input = route.request().postDataJSON();
      if (input.kind === 'SELL' && failSale)
        return respond({ detail: 'Satış kaydedilemedi. Tekrar deneyin.' }, 400);
      transactions.push({
        id: transactions.length + 1,
        ...input,
        created_at: metadata.server_time,
      });
      position = {
        symbol: input.symbol,
        quantity: input.quantity,
        avg_cost: input.price,
        current_price: 100,
        opened_at: metadata.server_time,
        hold_days: 1,
        realized_pnl: 0,
        unrealized_pnl: 100,
        unrealized_pnl_pct: 11.11,
        stop_loss: input.stop_loss ?? null,
        target: input.target ?? null,
        note: input.note ?? '',
        as_of: metadata.server_time,
        price_kind: 'delayed',
      };
      return respond({ position });
    }
    if (path.startsWith('/portfolio/') && route.request().method() === 'PATCH') {
      position = { ...position, ...route.request().postDataJSON() };
      return respond({ position });
    }
    if (path === '/chat')
      return route.fulfill({
        contentType: 'application/x-ndjson',
        body: '{"type":"tool","name":"get_daily_indicators"}\n{"type":"text","content":"**THYAO** için günlük trend yükseliş yönünde. Türkçe yanıt."}\n',
      });
    if (path === '/reset') return respond({ ok: true });
    return respond({ detail: 'Unknown path' }, 404);
  });
}

test('watchlist search, stock details, chart range and assistant flow', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await mockApi(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /Önce veriye bak/ })).toBeVisible();
  await page.getByRole('button', { name: 'Takip listesi', exact: true }).click();
  await page.getByRole('textbox', { name: 'Hisse ara' }).fill('Türk');
  await expect(page.locator('tbody tr')).toHaveCount(1);
  await page.getByRole('button', { name: 'THYAO detaylarını aç' }).click();
  await expect(page.getByRole('heading', { name: 'THYAO', exact: true })).toBeVisible();
  await expect(page.locator('canvas').first()).toBeVisible();
  await page.getByRole('button', { name: '15 dk', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Bu aralıkta veri yok' })).toBeVisible();
  await page.getByRole('button', { name: 'Asistana sor', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Asistana mesaj' })).toHaveValue(/THYAO/);
  await page.getByRole('button', { name: 'Mesajı gönder' }).click();
  await expect(page.locator('.chat-message.assistant')).toContainText('Türkçe yanıt.');
  await page.getByRole('button', { name: 'Asistanı kapat' }).click();
  expect(errors).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test('buy, edit plan, and read transaction history', async ({ page }) => {
  await mockApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Portföyüm', exact: true }).click();
  await page.getByRole('button', { name: 'Alım kaydet', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Hisse sembolü').fill('THYAO');
  await dialog.getByRole('spinbutton', { name: 'Lot', exact: true }).fill('10');
  await dialog.getByRole('spinbutton', { name: 'İşlem fiyatı / TL' }).fill('90');
  await dialog.getByRole('spinbutton', { name: 'Stop / TL' }).fill('85');
  await dialog.getByRole('button', { name: 'Alımı kaydet' }).click();
  await expect(page.locator('.position-card')).toContainText('THYAO');
  await page.getByRole('button', { name: 'Planı düzenle' }).click();
  await dialog.getByRole('spinbutton', { name: 'Hedef / TL' }).fill('120');
  await dialog.getByRole('textbox', { name: /Pozisyon notu/ }).fill('Hedef güncellendi');
  await dialog.getByRole('button', { name: 'Planı güncelle' }).click();
  await expect(page.locator('.position-note')).toHaveText('Hedef güncellendi');
  await page.getByRole('button', { name: 'Geçmiş', exact: true }).click();
  await expect(dialog).toContainText('Alım');
  await expect(dialog).toContainText('90,00');
  await page.getByRole('button', { name: 'Geçmişi kapat' }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test('watchlist selects catalog stocks, removes them and keeps changes on reload', async ({
  page,
}) => {
  await mockApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Takip listesi', exact: true }).click();
  const selector = page.getByRole('combobox', { name: 'Takip edilecek hisse' });
  await expect(selector.locator('option')).toHaveCount(2);
  await selector.selectOption('TUPRS');
  await page.getByRole('button', { name: 'Hisse ekle', exact: true }).click();
  await expect(page.locator('tbody tr')).toHaveCount(4);
  await expect(page.getByRole('button', { name: 'TUPRS detaylarını aç' })).toBeVisible();
  await expect(selector.locator('option[value="TUPRS"]')).toHaveCount(0);

  await page.getByRole('button', { name: 'THYAO takipten çıkar', exact: true }).click();
  await expect(page.locator('tbody tr')).toHaveCount(3);
  await expect(page.getByRole('button', { name: 'THYAO detaylarını aç' })).toHaveCount(0);
  await expect(selector.locator('option[value="THYAO"]')).toHaveCount(1);
  await page.reload();
  await page.getByRole('button', { name: 'Takip listesi', exact: true }).click();
  await expect(page.getByRole('button', { name: 'TUPRS detaylarını aç' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'THYAO detaylarını aç' })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test('watchlist update failure displays the error and preserves the list', async ({ page }) => {
  await mockApi(page, { failWatchlist: true });
  await page.goto('/');
  await page.getByRole('button', { name: 'Takip listesi', exact: true }).click();
  await page.getByRole('combobox', { name: 'Takip edilecek hisse' }).selectOption('TUPRS');
  await page.getByRole('button', { name: 'Hisse ekle', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Takip listesi güncellenemedi.');
  await expect(page.locator('tbody tr')).toHaveCount(3);
  await expect(page.getByRole('button', { name: 'TUPRS detaylarını aç' })).toHaveCount(0);
});

test('failed sale keeps the form open and displays the engine error', async ({ page }) => {
  await mockApi(page, { initialPosition: true, failSale: true });
  await page.goto('/');
  await page.getByRole('button', { name: 'Portföyüm', exact: true }).click();
  await page.getByRole('button', { name: 'Satış kaydet' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('spinbutton', { name: 'İşlem fiyatı / TL' }).fill('110');
  await dialog.getByRole('button', { name: 'Satışı kaydet' }).click();
  await expect(dialog.getByRole('alert')).toContainText('Satış kaydedilemedi');
  await expect(dialog).toBeVisible();
  await expect(page.locator('.position-card')).toContainText('10 lot');
});

test('login failure, successful login and logout', async ({ page }) => {
  await mockApi(page, { loginRequired: true });
  await page.goto('/');
  await page.getByLabel('Parola', { exact: true }).fill('wrong');
  await page.getByRole('button', { name: 'Giriş yap', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Parola doğru değil.');
  await page.getByLabel('Parola', { exact: true }).fill('correct');
  await page.getByRole('button', { name: 'Giriş yap', exact: true }).click();
  await expect(page.getByRole('heading', { name: /Önce veriye bak/ })).toBeVisible();
  await page.getByRole('button', { name: 'Çıkış yap', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Çalışma alanına giriş' })).toBeVisible();
});

test('holiday shows the reason, next session and closing price', async ({ page }) => {
  await mockApi(page, {
    quoteKind: 'close',
    metadataOverride: {
      server_time: '2026-10-29T15:00:00+03:00',
      session_date: '2026-10-29',
      day_kind: 'closed',
      reason: 'Cumhuriyet Bayramı',
      scheduled_open: false,
      session_start: null,
      session_end: null,
      daily_close: null,
      next_open: '2026-10-30T10:00:00+03:00',
    },
  });
  await page.goto('/');
  await expect(page.locator('.market-calendar-note')).toContainText('Cumhuriyet Bayramı');
  await expect(page.locator('.market-calendar-note')).toContainText('30 Eki 2026 10:00');
  await page.getByRole('button', { name: 'Takip listesi', exact: true }).click();
  await expect(page.locator('tbody')).toContainText('Son kapanış');
  await page.getByRole('button', { name: 'THYAO detaylarını aç' }).click();
  await page.getByRole('button', { name: '15 dk', exact: true }).click();
  await expect(page.locator('.empty')).toContainText('Bugün seans yok: Cumhuriyet Bayramı');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test('half day shows the actual hours after early close', async ({ page }) => {
  await mockApi(page, {
    metadataOverride: {
      server_time: '2026-10-28T12:45:00+03:00',
      session_date: '2026-10-28',
      day_kind: 'half_day',
      reason: 'Cumhuriyet Bayramı arifesi',
      scheduled_open: false,
      session_end: '12:30',
      daily_close: '12:40',
      next_open: '2026-10-30T10:00:00+03:00',
    },
  });
  await page.goto('/');
  await expect(page.locator('.market-calendar-note')).toContainText(
    'Yarım gün · 10:00–12:30 · Kapalı',
  );
  await page.getByRole('button', { name: 'Takip listesi', exact: true }).click();
  await page.getByRole('button', { name: 'THYAO detaylarını aç' }).click();
  await page.getByRole('button', { name: '15 dk', exact: true }).click();
  await expect(page.locator('.empty')).toContainText('Yarım gün seansı 12:30 saatinde biter.');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test('unknown calendar state is visible without claiming an open market', async ({ page }) => {
  await mockApi(page, {
    metadataOverride: {
      day_kind: 'unknown',
      reason: 'Takvim bu yılı kapsamıyor.',
      scheduled_open: null,
      session_start: null,
      session_end: null,
      daily_close: null,
      next_open: null,
    },
  });
  await page.goto('/');
  await expect(page.locator('.market-calendar-note')).toContainText('Takvim kapsamı dışında');
  await expect(page.locator('.topbar .status-dot')).toHaveClass(/closed/);
});

test('timestamps use the timezone supplied by metadata', async ({ page }) => {
  await mockApi(page, { metadataOverride: { timezone: 'America/New_York' } });
  await page.goto('/');
  await expect(page.locator('.workspace-meta')).toContainText('America/New_York');
  await expect(page.locator('.news-meta')).toContainText('06 Eki 2026');
  await page.getByRole('button', { name: 'Takip listesi', exact: true }).click();
  await expect(page.locator('tbody')).toContainText('07:45');
  await page.getByRole('button', { name: 'THYAO detaylarını aç' }).click();
  await expect(page.locator('.symbol-price')).toContainText('07:45');
});
