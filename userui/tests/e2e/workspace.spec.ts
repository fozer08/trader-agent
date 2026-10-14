import { horizons, risks } from '../../src/preferences';
import { test, expect, type Page } from '@playwright/test';
import type { Metadata } from '../../src/types';
import { validateSettings } from '../../backend/settings.mjs';

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
  horizon: 'swing',
  as_of: '2026-10-05T10:00:00+03:00',
  price_vs_ema: 'above',
  ema_fast_period: 20,
  ema_slow_period: 50,
  ema_alignment: 'bullish',
  ema_fast: 95,
  ema_slow: 90,
  rsi: 56.4,
  atr: 3.5,
  atr_pct: 3.5,
  return_20_pct: 5,
  unavailable: {},
  levels: {
    last_high: 103,
    last_low: 97,
    last_close: 99,
    rolling_5_high: 106,
    rolling_5_low: 93,
    rolling_20_high: 110,
    rolling_20_low: 90,
    rolling_60_high: 120,
    rolling_60_low: 85,
  },
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
  dailyLine?: boolean;
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
    dailyLine = false,
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
        id: 'position-1',
        source: 'manual',
        producer: null,
        recommendation_id: null,
        initial_plan: { stop_loss: 85, target: 120, horizon: 'position', note: 'Test pozisyonu' },
        plan_changed: false,
        created_at: metadata.server_time,
        closed_at: null,
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
        horizon: 'position',
        distance_to_stop_pct: 15,
        distance_to_target_pct: 20,
        as_of: '2026-10-06T14:45:00+03:00',
        price_kind: 'delayed',
      }
    : undefined;
  let preferences = { horizon: null as string | null, risk_level: null as string | null };
  let settings = {
    sidebarCollapsed: (page.viewportSize()?.width ?? 1440) < 700,
    chatWidth: 420,
    markets: ['bist100', 'usdtry', 'gold'],
  };
  let conversationId = 'chat-1';
  const transactions: Record<string, unknown>[] = [];
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname.slice(4);
    const respond = (json: unknown, status = 200) => route.fulfill({ status, json });
    if (path === '/session')
      return respond({
        authenticated,
        login_required: loginRequired,
        user: authenticated ? { id: 'user-1', username: 'trader' } : null,
      });
    if (path === '/settings') {
      if (route.request().method() === 'PUT') {
        try {
          settings = validateSettings(route.request().postDataJSON());
        } catch (error) {
          return respond(
            { detail: error instanceof Error ? error.message : 'Geçersiz tercihler.' },
            400,
          );
        }
      }
      return respond(settings);
    }
    if (path === '/preferences') {
      if (route.request().method() === 'PUT') preferences = route.request().postDataJSON();
      return respond(preferences);
    }
    if (path === '/history') return respond({ conversationId, messages: [] });
    if (path === '/conversations')
      return respond({ items: [{ id: conversationId, title: 'Yeni sohbet' }] });
    if (path === '/markets')
      return respond({
        items: [
          ['bist100', 'BIST 100'],
          ['usdtry', 'USD / TL'],
          ['gold', 'Altın (ons)'],
          ['bitcoin', 'Bitcoin'],
        ].map(([id, name]) => ({ id, name, price: null, change_pct: null })),
      });
    if (path === '/register') {
      authenticated = true;
      return respond({ ok: true });
    }
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
        chart_type: timeframe === 'D1' && !dailyLine ? 'candlestick' : 'line',
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
        horizon: new URL(route.request().url()).searchParams.get('horizon') ?? 'swing',
        daily: {
          ...indicators,
          horizon: new URL(route.request().url()).searchParams.get('horizon') ?? 'swing',
        },
        delay_minutes: 15,
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
    if (path === '/recommendations') return respond({ items: [] });
    if (path === '/portfolio/preview') {
      const v = route.request().postDataJSON();
      return respond({
        amount: v.quantity * v.price,
        planned_loss: v.stop_loss ? v.quantity * (v.price - v.stop_loss) : null,
        planned_gain: v.target ? v.quantity * (v.target - v.price) : null,
        realized_pnl: v.avg_cost ? v.quantity * (v.price - v.avg_cost) : null,
      });
    }
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
          missing_stops: 0,
          breached_stops: 0,
          planned_downside_to_stops: position ? 50 : 0,
          largest_position_weight_pct: position ? 100 : null,
        },
      });
    if (path === '/portfolio/closed') return respond({ closed: [] });
    if (path.endsWith('/transactions') && route.request().method() === 'GET')
      return respond({ transactions, plans: [], initial_plan: position?.initial_plan });
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
        id: 'position-1',
        source: 'manual',
        producer: null,
        recommendation_id: null,
        initial_plan: {
          stop_loss: input.stop_loss,
          target: input.target,
          horizon: input.horizon,
          note: input.note,
        },
        plan_changed: false,
        created_at: metadata.server_time,
        closed_at: null,
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
        horizon: input.horizon,
        as_of: metadata.server_time,
        price_kind: 'delayed',
      };
      return respond({ position });
    }
    if (path.startsWith('/portfolio/') && route.request().method() === 'PATCH') {
      position = { ...position, ...route.request().postDataJSON(), plan_changed: true };
      return respond({ position });
    }
    if (path.startsWith('/portfolio/') && route.request().method() === 'GET')
      return respond(position);
    if (path === '/chat')
      return route.fulfill({
        contentType: 'application/x-ndjson',
        body: '{"type":"tool","name":"analyze"}\n{"type":"text","content":"**THYAO** için günlük trend yükseliş yönünde. Türkçe yanıt."}\n',
      });
    if (path === '/reset') {
      conversationId = 'chat-2';
      return respond({ conversationId });
    }
    return respond({ detail: 'Unknown path' }, 404);
  });
}

test('watchlist search, stock details, chart range and assistant flow', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await mockApi(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /Önce veriye bak/ })).toBeVisible();
  await navigate(page, 'Takip listesi');
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
  await navigate(page, 'Portföyüm');
  await page.getByRole('button', { name: 'Manuel pozisyon ekle', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Hisse sembolü').fill('THYAO');
  await dialog.getByRole('spinbutton', { name: 'Lot', exact: true }).fill('10');
  await dialog.getByRole('spinbutton', { name: 'Giriş fiyatı / TL' }).fill('90');
  await dialog.getByText('Stop, hedef ve diğer bilgiler').click();
  await dialog.getByRole('spinbutton', { name: 'Stop / TL' }).fill('85');
  await dialog.getByLabel('Pozisyon vadesi').selectOption('swing');
  await dialog.getByRole('button', { name: 'Pozisyonu kaydet' }).click();
  await expect(page.locator('.position-card')).toContainText('THYAO');
  await expect(page.locator('.position-card')).toContainText('Vade: Günler–haftalar');
  await page.getByRole('button', { name: 'THYAO pozisyonunu yönet' }).click();
  await page.getByRole('button', { name: 'Planı düzenle' }).click();
  await dialog.getByRole('spinbutton', { name: 'Hedef / TL' }).fill('120');
  await dialog.getByRole('textbox', { name: /Pozisyon notu/ }).fill('Hedef güncellendi');
  await dialog.getByLabel('Pozisyon vadesi').selectOption('position');
  await dialog.getByRole('button', { name: 'Planı güncelle' }).click();
  await expect(page.locator('.position-card')).toContainText('Plan düzenlendi');
  await expect(page.locator('.position-card')).toContainText('Vade: 1–3 ay');
  await page.getByRole('button', { name: 'THYAO pozisyonunu yönet' }).click();
  await expect(dialog).toContainText('Ekleme');
  await expect(dialog).toContainText('90,00');
  await page.getByRole('button', { name: 'Pencereyi kapat' }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test('watchlist selects catalog stocks, removes them and keeps changes on reload', async ({
  page,
}) => {
  await mockApi(page);
  await page.goto('/');
  await navigate(page, 'Takip listesi');
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
  await navigate(page, 'Takip listesi');
  await expect(page.getByRole('button', { name: 'TUPRS detaylarını aç' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'THYAO detaylarını aç' })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test('watchlist update failure displays the error and preserves the list', async ({ page }) => {
  await mockApi(page, { failWatchlist: true });
  await page.goto('/');
  await navigate(page, 'Takip listesi');
  await page.getByRole('combobox', { name: 'Takip edilecek hisse' }).selectOption('TUPRS');
  await page.getByRole('button', { name: 'Hisse ekle', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Takip listesi güncellenemedi.');
  await expect(page.locator('tbody tr')).toHaveCount(3);
  await expect(page.getByRole('button', { name: 'TUPRS detaylarını aç' })).toHaveCount(0);
});

test('failed sale keeps the form open and displays the engine error', async ({ page }) => {
  await mockApi(page, { initialPosition: true, failSale: true });
  await page.goto('/');
  await navigate(page, 'Portföyüm');
  await page.getByRole('tab', { name: 'Açık pozisyonlar' }).click();
  await page.getByRole('button', { name: 'THYAO pozisyonunu yönet' }).click();
  await page.getByRole('button', { name: 'Pozisyonu kapat' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('spinbutton', { name: 'Kapatma fiyatı / TL' }).fill('110');
  await dialog.getByRole('button', { name: 'Kapatmayı kaydet' }).click();
  await expect(dialog.getByRole('alert')).toContainText('Satış kaydedilemedi');
  await expect(dialog).toBeVisible();
  await expect(page.locator('.position-card')).toContainText('10 lot');
});

test('login failure, successful login and logout', async ({ page }) => {
  await mockApi(page, { loginRequired: true });
  await page.goto('/');
  await page.getByLabel('Kullanıcı adı', { exact: true }).fill('trader');
  await page.getByLabel('Parola', { exact: true }).fill('wrong');
  await page.getByRole('button', { name: 'Giriş yap', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Parola doğru değil.');
  await page.getByLabel('Parola', { exact: true }).fill('correct');
  await page.getByRole('button', { name: 'Giriş yap', exact: true }).click();
  await expect(page.getByRole('heading', { name: /Önce veriye bak/ })).toBeVisible();
  await page.getByRole('button', { name: 'Çıkış yap', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Tekrar hoş geldin' })).toBeVisible();
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
  await navigate(page, 'Takip listesi');
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
  await navigate(page, 'Takip listesi');
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
  await navigate(page, 'Takip listesi');
  await expect(page.locator('tbody')).toContainText('07:45');
  await page.getByRole('button', { name: 'THYAO detaylarını aç' }).click();
  await expect(page.locator('.symbol-price')).toContainText('07:45');
});

async function setPreferences(page: Page, horizon: string, risk: string) {
  await navigate(page, 'Tercihler');
  const horizonLabel = horizons.find(([key]) => key === horizon)![1];
  const riskLabel = risks.find(([key]) => key === risk)![1];
  const horizonControl = page.getByRole('radio', { name: horizonLabel, exact: true });
  await horizonControl.click();
  await expect(horizonControl).toBeChecked();
  const riskControl = page.getByRole('radio', { name: riskLabel, exact: true });
  await riskControl.click();
  await expect(riskControl).toBeChecked();
}
async function navigate(page: Page, name: string) {
  await page
    .getByRole('navigation', { name: 'Çalışma alanı' })
    .getByRole('button', { name, exact: true })
    .click();
}

test('central preferences drive horizon metrics and chat without inline selections', async ({
  page,
}) => {
  await mockApi(page);
  await page.goto('/');
  await setPreferences(page, 'position', 'high');
  await navigate(page, 'Takip listesi');
  await page.getByRole('button', { name: 'THYAO detaylarını aç' }).click();
  await expect(page.locator('.technical-metrics')).toContainText('60 seans getirisi');
  await expect(page.locator('.technical-metrics')).toContainText('Göreli hacim');
  await page.getByRole('button', { name: '5 dk', exact: true }).click();
  await expect(page.locator('.analysis-preferences')).toContainText('1–3 ay');
  await page.getByRole('button', { name: 'Asistana sor', exact: true }).click();
  const chat = page.getByRole('complementary', { name: 'Analiz asistanı' });
  await expect(chat.getByLabel('İşlem vadesi')).toHaveCount(0);
  await expect(chat.getByLabel('Risk Seviyesi')).toHaveCount(0);
  const sent = page.waitForRequest('**/api/chat');
  await chat.getByRole('button', { name: 'Mesajı gönder' }).click();
  expect((await sent).postDataJSON().preferences).toEqual({
    horizon: 'position',
    risk_level: 'high',
  });
});

test('new chat and reload preserve trading and display preferences', async ({ page }) => {
  await mockApi(page);
  await page.goto('/');
  await setPreferences(page, 'intraday', 'low');
  await page.getByRole('button', { name: 'Asistan', exact: true }).click();
  const chat = page.getByRole('complementary', { name: 'Analiz asistanı' });
  await chat.getByRole('textbox', { name: 'Asistana mesaj' }).fill('İlk değerlendirme');
  await chat.getByRole('button', { name: 'Mesajı gönder' }).click();
  await expect(chat.locator('.chat-message.assistant')).toContainText('Türkçe yanıt');
  await chat.getByRole('button', { name: 'Yeni sohbet' }).click();
  await expect(chat.locator('.chat-message')).toHaveCount(0);
  await chat.getByRole('button', { name: 'Asistanı kapat' }).click();
  await page.reload();
  await navigate(page, 'Tercihler');
  await expect(page.getByRole('radio', { name: 'Seans içi', exact: true })).toBeChecked();
  await expect(page.getByRole('radio', { name: 'Düşük', exact: true })).toBeChecked();
});

test('icon rail opens central preferences and preserves market choices', async ({
  page,
}, testInfo) => {
  await mockApi(page);
  await page.goto('/');
  const menu = page.getByRole('complementary', { name: 'Ana menü' });
  await expect(menu).toBeVisible();
  if (await page.getByRole('button', { name: 'Menüyü daralt', exact: true }).isVisible())
    await page.getByRole('button', { name: 'Menüyü daralt', exact: true }).click();
  await expect(menu).toHaveCSS('width', (page.viewportSize()?.width ?? 0) <= 700 ? '60px' : '72px');
  await expect(menu.getByRole('button', { name: 'Takip listesi', exact: true })).toBeVisible();
  await expect(menu.locator('.nav-text').first()).not.toBeVisible();
  await navigate(page, 'Tercihler');
  await expect(
    page.getByRole('main').getByRole('heading', { name: 'Tercihler', exact: true }),
  ).toBeVisible();
  await expect(menu.getByRole('button', { name: 'Tercihler', exact: true })).toHaveAttribute(
    'aria-current',
    'page',
  );
  await expect(menu.locator('details')).toHaveCount(0);
  await expect(page.getByRole('group', { name: 'İşlem vadesi' })).toBeVisible();
  await expect(page.getByRole('group', { name: 'Risk Seviyesi' })).toBeVisible();
  await page.getByRole('checkbox', { name: 'Bitcoin', exact: true }).check();
  await expect(page.getByRole('checkbox', { name: 'Bitcoin', exact: true })).toBeChecked();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('preferences.png'), fullPage: true });
  await page.getByRole('button', { name: 'Menüyü genişlet', exact: true }).click();
  await expect(menu.locator('.nav-text').first()).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('expanded-menu.png') });
  await page.getByRole('button', { name: 'Menüyü daralt', exact: true }).click();
  await navigate(page, 'Genel bakış');
  await expect(page.getByRole('region', { name: 'Piyasa özeti' })).toContainText('Bitcoin');
  await page.reload();
  await expect(page.getByRole('button', { name: 'Menüyü genişlet', exact: true })).toBeVisible();
  await navigate(page, 'Tercihler');
  await expect(page.getByRole('checkbox', { name: 'Bitcoin', exact: true })).toBeChecked();
  await page.getByRole('button', { name: 'Asistan', exact: true }).click();
  if ((page.viewportSize()?.width ?? 0) > 700) {
    const separator = page.getByRole('separator', { name: 'Sohbet genişliği' });
    await separator.focus();
    await separator.press('ArrowLeft');
    await expect(separator).toHaveAttribute('aria-valuenow', '460');
    await separator.press('ArrowRight');
    await expect(separator).toHaveAttribute('aria-valuenow', '420');
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('portfolio review and additions preserve the selected position horizon', async ({ page }) => {
  await mockApi(page, { initialPosition: true });
  await page.goto('/');
  await navigate(page, 'Portföyüm');
  await page.getByRole('tab', { name: 'Açık pozisyonlar' }).click();
  await expect(page.locator('.position-card')).toContainText('Vade: 1–3 ay');
  await page.getByRole('button', { name: 'THYAO pozisyonunu yönet' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Asistanla değerlendir' }).click();
  const chat = page.getByRole('complementary', { name: 'Analiz asistanı' });
  await expect(chat.getByRole('textbox', { name: 'Asistana mesaj' })).toHaveValue(
    /position-1 kimlikli pozisyonumu kendi kayıtlı vadesine/,
  );
  await chat.getByRole('button', { name: 'Asistanı kapat' }).click();
  await page.getByRole('button', { name: 'THYAO pozisyonunu yönet' }).click();
  await page.getByRole('button', { name: 'Miktar artır' }).click();
  await expect(page.getByRole('dialog')).toContainText('Vade: 1–3 ay');
  await expect(page.getByRole('dialog').getByLabel('Pozisyon vadesi')).toHaveCount(0);
});

test('new position cannot be saved without a horizon', async ({ page }) => {
  await mockApi(page);
  await page.goto('/');
  await navigate(page, 'Portföyüm');
  await page.getByRole('button', { name: 'Manuel pozisyon ekle', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Hisse sembolü').fill('THYAO');
  await dialog.getByRole('spinbutton', { name: 'Lot', exact: true }).fill('1');
  await dialog.getByRole('spinbutton', { name: 'Giriş fiyatı / TL' }).fill('100');
  await dialog.getByRole('button', { name: 'Pozisyonu kaydet' }).click();
  await expect(dialog).toBeVisible();
  expect(
    await dialog
      .getByLabel('Pozisyon vadesi')
      .evaluate((element: HTMLSelectElement) => element.validity.valueMissing),
  ).toBe(true);
  await dialog.getByLabel('Pozisyon vadesi').selectOption('intraday');
  const sent = page.waitForRequest('**/api/portfolio/transactions');
  await dialog.getByRole('button', { name: 'Pozisyonu kaydet' }).click();
  expect((await sent).postDataJSON().horizon).toBe('intraday');
  await expect(page.locator('.position-card')).toContainText('Vade: Seans içi');
});

test('incomplete daily OHLC displays a closing-price chart', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await mockApi(page, { dailyLine: true });
  await page.goto('/');
  await navigate(page, 'Takip listesi');
  await page.getByRole('button', { name: 'THYAO detaylarını aç' }).click();
  await expect(page.locator('canvas').first()).toBeVisible();
  await expect(page.locator('.chart-caption')).toContainText('Kapanış fiyatları');
  expect(errors).toEqual([]);
});

test('simple registration and responsive workspace preview', async ({ page }, testInfo) => {
  await mockApi(page, { loginRequired: true });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Tekrar hoş geldin' })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('login.png'), fullPage: true });
  await page.getByRole('button', { name: 'Yeni misin? Hesap oluştur' }).click();
  await page.getByLabel('Kullanıcı adı', { exact: true }).fill('new_trader');
  await page.getByLabel('Parola', { exact: true }).fill('new-password');
  await page.getByRole('button', { name: 'Hesap oluştur', exact: true }).click();
  await expect(page.getByRole('heading', { name: /Önce veriye bak/ })).toBeVisible();
  await page.getByRole('button', { name: 'Asistan', exact: true }).click();
  if ((page.viewportSize()?.width ?? 0) > 700) {
    const handle = page.getByRole('separator', { name: 'Sohbet genişliği' });
    const before = await handle.boundingBox();
    await page.mouse.move(before!.x + 5, 300);
    await page.mouse.down();
    await page.mouse.move(page.viewportSize()!.width - 520, 300, { steps: 5 });
    await page.mouse.up();
    await expect(handle).toHaveAttribute('aria-valuenow', '520');
  }
  await page.screenshot({ path: testInfo.outputPath('workspace.png') });
});

test('fractional pointer coordinates save a whole-pixel chat width and keep later settings valid', async ({
  page,
}) => {
  test.skip((page.viewportSize()?.width ?? 0) <= 700, 'Mobil sohbet tam ekran.');
  await mockApi(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Asistan', exact: true }).click();
  const handle = page.getByRole('separator', { name: 'Sohbet genişliği' });
  const box = await handle.boundingBox();
  await page.mouse.move(box!.x + 5, 300);
  await page.mouse.down();
  await handle.dispatchEvent('pointermove', {
    pointerId: 1,
    clientX: page.viewportSize()!.width - 519.5,
    clientY: 300,
    buttons: 1,
  });
  const saved = page.waitForResponse(
    (response) => response.url().endsWith('/api/settings') && response.request().method() === 'PUT',
  );
  await page.mouse.up();
  const response = await saved;
  expect(response.status()).toBe(200);
  expect(response.request().postDataJSON().chatWidth).toBe(520);
  await expect(handle).toHaveAttribute('aria-valuenow', '520');
  const collapsed = page.waitForResponse(
    (response) => response.url().endsWith('/api/settings') && response.request().method() === 'PUT',
  );
  await page.getByRole('button', { name: 'Menüyü daralt', exact: true }).click();
  expect((await collapsed).status()).toBe(200);
  await page.reload();
  await page.getByRole('button', { name: 'Asistan', exact: true }).click();
  await expect(handle).toHaveAttribute('aria-valuenow', '520');
  await expect(page.getByRole('button', { name: 'Menüyü genişlet', exact: true })).toBeVisible();
  await expect(page.getByText('Ekran tercihleri kaydedilemedi.', { exact: false })).toHaveCount(0);
});
