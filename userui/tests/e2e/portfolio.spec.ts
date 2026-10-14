import { expect, test, type Page } from '@playwright/test';
const now = new Date('2026-10-06T12:00:00Z');
const recId = '33333333-3333-4333-8333-333333333333';
async function mockPortfolio(page: Page, { expireOnAccept = false, initialManual = false } = {}) {
  await page.clock.setFixedTime(now);
  let settings = {
    sidebarCollapsed: true,
    chatWidth: 420,
    markets: [],
    portfolioTab: 'recommended',
  };
  const rec = {
    id: recId,
    symbol: 'THYAO',
    entry_min: 100,
    entry_max: 105,
    stop_loss: 95,
    target: 120,
    horizon: 'swing',
    risk_level: 'low',
    rationale: 'Günlük trend ve fiyat aralığı birlikte değerlendirildi.',
    producer: 'assistant',
    producer_version: 'test',
    created_at: now.toISOString(),
    data_as_of: '2026-10-06T11:45:00Z',
    expires_at: '2026-10-06T21:00:00Z',
    active: true,
    position_id: null as string | null,
  };
  const makePosition = (id: string, source: string, quantity: number, price: number) => ({
    id,
    symbol: 'THYAO',
    source,
    producer: source === 'system' ? 'assistant' : null,
    recommendation_id: source === 'system' ? recId : null,
    initial_plan: { ...rec, note: '' },
    plan_changed: false,
    created_at: now.toISOString(),
    closed_at: null as string | null,
    quantity,
    avg_cost: price,
    current_price: 110,
    opened_at: now.toISOString(),
    hold_days: 1,
    realized_pnl: 0,
    unrealized_pnl: quantity * (110 - price),
    unrealized_pnl_pct: ((110 - price) / price) * 100,
    horizon: 'swing',
    stop_loss: 95,
    target: 120,
    note: '',
    as_of: now.toISOString(),
    price_kind: 'delayed',
    distance_to_stop_pct: 13.64,
    distance_to_target_pct: 9.09,
  });
  const positions = initialManual ? [makePosition('manual-1', 'manual', 5, 115)] : [];
  const moves: {
    position_id: string;
    id: number;
    kind: string;
    quantity: number;
    price: number;
    created_at: string;
  }[] = [];
  const messages: { id: string; role: string; text: string; recommendationIds?: string[] }[] = [];
  let acceptCalls = 0;
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.slice(4);
    const method = route.request().method();
    const send = (json: unknown, status = 200) => route.fulfill({ json, status });
    const selected = positions.filter(
      (p) => !url.searchParams.get('source') || p.source === url.searchParams.get('source'),
    );
    const active = selected.filter((p) => p.closed_at === null);
    const summary = {
      total_cost: active.reduce((s, p) => s + p.quantity * p.avg_cost, 0),
      market_value: active.reduce((s, p) => s + p.quantity * 110, 0),
      unrealized_pnl: active.reduce((s, p) => s + p.quantity * (110 - p.avg_cost), 0),
      unrealized_pnl_pct: 5,
      realized_pnl: selected.reduce((s, p) => s + p.realized_pnl, 0),
      missing_prices: 0,
      missing_stops: 0,
      breached_stops: 0,
      planned_downside_to_stops: 15,
      largest_position_weight_pct: 100,
    };
    if (path === '/session')
      return send({
        authenticated: true,
        login_required: true,
        user: { id: 'user-1', username: 'trader' },
      });
    if (path === '/preferences') return send({ horizon: 'swing', risk_level: 'low' });
    if (path === '/settings') {
      if (method === 'PUT') settings = route.request().postDataJSON();
      return send(settings);
    }
    if (path === '/markets') return send({ items: [] });
    if (path === '/meta')
      return send({
        server_time: now.toISOString(),
        timezone: 'Europe/Istanbul',
        source: 'test',
        delay_minutes: 15,
        day_kind: 'normal',
        scheduled_open: true,
        session_date: '2026-10-06',
        watchlist_count: 0,
        session_start: '10:00',
        session_end: '18:00',
      });
    if (path === '/watchlist')
      return send({ items: [], refreshing: false, updated_at: now.toISOString() });
    if (path === '/stocklist')
      return send({ items: [{ symbol: 'THYAO', name: 'Türk Hava Yolları' }] });
    if (path === '/news') return send({ digests: [] });
    if (path === '/recommendations') return send({ items: [rec] });
    if (path === `/recommendations/${recId}`) return send(rec);
    if (path.endsWith('/accept')) {
      acceptCalls += 1;
      const input = route.request().postDataJSON();
      if (expireOnAccept)
        return send({ detail: 'Bu önerinin süresi doldu. Yeni önerileri inceleyin.' }, 400);
      if (input.price < 100 || input.price > 105)
        return send({ detail: 'Giriş fiyatı 100–105 TL aralığında olmalı.' }, 400);
      const p = makePosition('system-1', 'system', input.quantity, input.price);
      rec.position_id = p.id;
      positions.push(p);
      moves.push({
        position_id: p.id,
        id: 1,
        kind: 'BUY',
        quantity: input.quantity,
        price: input.price,
        created_at: now.toISOString(),
      });
      return send({ position: p });
    }
    if (path === '/portfolio/preview') {
      const v = route.request().postDataJSON();
      return send({
        amount: v.quantity * v.price,
        planned_loss: v.stop_loss ? v.quantity * (v.price - v.stop_loss) : null,
        planned_gain: v.target ? v.quantity * (v.target - v.price) : null,
        realized_pnl: v.avg_cost ? v.quantity * (v.price - v.avg_cost) : null,
      });
    }
    if (path === '/portfolio') return send({ positions: active, summary });
    if (path === '/portfolio/closed')
      return send({
        closed: selected
          .filter((p) => p.closed_at)
          .map((p) => ({ ...p, quantity: 10, realized_pnl_pct: 7.84 })),
      });
    if (path === '/portfolio/performance')
      return send({
        realized_pnl: summary.realized_pnl,
        released_cost: 1020,
        realized_return_pct: 7.84,
        closed_count: selected.filter((p) => p.closed_at).length,
        wins: selected.filter((p) => p.closed_at && p.realized_pnl > 0).length,
        losses: 0,
        breakeven: 0,
        win_rate: 100,
        average_hold_days: 1,
        known_hold_count: 1,
        current: summary,
      });
    if (path === '/portfolio/transactions') {
      const v = route.request().postDataJSON();
      const p = positions.find((p) => p.id === v.position_id)!;
      p.realized_pnl += v.quantity * (v.price - p.avg_cost);
      p.quantity -= v.quantity;
      if (!p.quantity) p.closed_at = now.toISOString();
      moves.push({ ...v, id: moves.length + 1, created_at: now.toISOString() });
      return send({ position: p.closed_at ? null : p });
    }
    if (path.startsWith('/portfolio/')) {
      const id = path.split('/')[2];
      const p = positions.find((p) => p.id === id);
      if (path.endsWith('/transactions'))
        return send({
          transactions: moves.filter((m) => m.position_id === id),
          plans: [],
          initial_plan: p?.initial_plan,
        });
      return send(p);
    }
    if (path.startsWith('/symbols/'))
      return send({ symbol: 'THYAO', price: 110, delay_minutes: 15 });
    if (path === '/history') return send({ conversationId: 'chat-1', messages });
    if (path === '/conversations') return send({ items: [{ id: 'chat-1', title: 'Günlük plan' }] });
    if (path === '/chat') {
      messages.push({
        id: 'assistant-1',
        role: 'assistant',
        text: 'Günlük planı hazırladım.',
        recommendationIds: [recId],
      });
      return route.fulfill({
        contentType: 'application/x-ndjson',
        body:
          JSON.stringify({ type: 'text', content: 'Günlük planı hazırladım.' }) +
          '\n' +
          JSON.stringify({ type: 'recommendation', recommendation_id: recId }) +
          '\n',
      });
    }
    return send({ detail: 'Unknown path: ' + path }, 404);
  });
  return { acceptCalls: () => acceptCalls };
}
async function openPortfolio(page: Page) {
  await page
    .getByRole('navigation')
    .getByRole('button', { name: 'Portföyüm', exact: true })
    .click();
}

test('recommendation opens with bounded price, preserves source and measures only portfolio', async ({
  page,
}, testInfo) => {
  const api = await mockPortfolio(page, { initialManual: true });
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/');
  await openPortfolio(page);
  const card = page.getByRole('article', { name: 'THYAO önerisi' });
  await expect(card).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('recommended.png'), fullPage: true });
  await card.getByRole('button', { name: 'Portföye dahil et' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Lot', { exact: true }).fill('10');
  const price = dialog.getByLabel('Giriş fiyatı / TL');
  await expect(price).toBeEmpty();
  await price.fill('99');
  await dialog.getByRole('button', { name: 'Portföye dahil et' }).click();
  expect(api.acceptCalls()).toBe(0);
  await expect(dialog).toBeVisible();
  await price.fill('102');
  await expect(dialog.locator('.entry-preview')).toContainText('1.020,00');
  await page.screenshot({ path: testInfo.outputPath('entry.png') });
  await dialog.getByRole('button', { name: 'Portföye dahil et' }).click();
  await expect(page.getByRole('tab', { name: 'Açık pozisyonlar' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.locator('.position-card')).toHaveCount(2);
  await expect(page.getByRole('article', { name: 'THYAO sistem pozisyonu' })).toContainText(
    'Sistem önerisi',
  );
  await page.getByRole('button', { name: 'Sistem kaynaklı', exact: true }).click();
  await expect(page.locator('.position-card')).toHaveCount(1);
  await page.getByRole('button', { name: 'THYAO pozisyonunu yönet' }).click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Pozisyonu kapat', exact: true })
    .click();
  await page.getByRole('dialog').getByLabel('Kapatma fiyatı / TL').fill('110');
  await page.getByRole('dialog').getByRole('button', { name: 'Kapatmayı kaydet' }).click();
  await page.getByRole('tab', { name: 'Performans', exact: true }).click();
  await page.getByRole('button', { name: 'Sistem kaynaklı', exact: true }).click();
  await expect(page.locator('.performance-grid')).toContainText('80,00');
  await expect(page.locator('.performance-outcomes')).toContainText('1 kârlı');
  await page.screenshot({ path: testInfo.outputPath('performance.png'), fullPage: true });
  await page.getByRole('button', { name: 'Manuel', exact: true }).click();
  await expect(page.locator('.performance-outcomes')).toContainText('0 kârlı');
  expect(api.acceptCalls()).toBe(1);
  expect(errors).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('expiry during entry preserves typed fields and does not open a position', async ({
  page,
}) => {
  await mockPortfolio(page, { expireOnAccept: true });
  await page.goto('/');
  await openPortfolio(page);
  await page.getByRole('button', { name: 'Portföye dahil et' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Lot', { exact: true }).fill('12');
  await dialog.getByLabel('Giriş fiyatı / TL').fill('103');
  await dialog.getByRole('button', { name: 'Portföye dahil et' }).click();
  await expect(dialog.getByRole('alert')).toContainText('süresi doldu');
  await expect(dialog.getByLabel('Lot', { exact: true })).toHaveValue('12');
  await expect(dialog.getByLabel('Giriş fiyatı / TL')).toHaveValue('103');
});

test('chat cards survive history reload and expired cards cannot be added', async ({ page }) => {
  await mockPortfolio(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Asistan', exact: true }).click();
  const chat = page.getByRole('complementary', { name: 'Analiz asistanı' });
  await chat.getByLabel('Asistana mesaj').fill('Bir plan oluştur.');
  await chat.getByRole('button', { name: 'Mesajı gönder' }).click();
  await expect(chat.getByRole('article', { name: 'THYAO önerisi' })).toBeVisible();
  await page.reload();
  await page.clock.setFixedTime(new Date('2026-10-07T00:00:00+03:00'));
  await page.getByRole('button', { name: 'Asistan', exact: true }).click();
  await expect(chat.getByRole('button', { name: 'Süresi doldu' })).toBeDisabled();
  await chat.getByRole('button', { name: 'Asistanı kapat' }).click();
  await openPortfolio(page);
  await expect(page.getByRole('article', { name: 'THYAO önerisi' })).toHaveCount(0);
});
