import { useEffect, useRef, useState } from 'react';
import { ArrowDownLeft, ArrowUpRight, Bot, Check, History, Plus, Settings2, X } from 'lucide-react';
import { useResource } from '../api';
import type {
  ClosedPosition,
  Performance,
  Portfolio as PortfolioData,
  PortfolioTab,
  Position,
  PositionSource,
  Recommendation,
  Transaction,
} from '../types';
import { horizons } from '../preferences';
import { money, number, percent, priceLabel, tone } from '../format';
import { useMarketFormat } from '../market';
import { Empty, ErrorNotice, Loading, PortfolioMetrics } from './Common';
import { SymbolMark } from './Watchlist';
import { RecommendationCard, RecommendationEmpty } from './Recommendations';
import type { Editor } from './PositionEditor';
export { TransactionEditor, type Editor } from './PositionEditor';

function SourceTag({ source, changed = false }: { source: PositionSource; changed?: boolean }) {
  return (
    <span className={`source-tag ${source}`}>
      <span>{source === 'system' ? 'Sistem önerisi' : 'Manuel'}</span>
      {changed && <span> · Plan düzenlendi</span>}
    </span>
  );
}
function PositionManager({
  id,
  current,
  close,
  edit,
  ask,
}: {
  id: string;
  current?: Position;
  close: () => void;
  edit: (editor: Editor) => void;
  ask: (text: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const info = useResource<Position>(current ? null : `/portfolio/${id}`);
  const history = useResource<{
    transactions: Transaction[];
    initial_plan: Position['initial_plan'];
    plans: {
      created_at: string;
      stop_loss: number | null;
      target: number | null;
      horizon: string;
      note: string;
    }[];
  }>(`/portfolio/${id}/transactions`);
  const p = current ?? info.data;
  const { date } = useMarketFormat();
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  const open = (mode: Editor['mode']) => {
    if (p) {
      close();
      edit({ mode, position: p, symbol: p.symbol });
    }
  };
  return (
    <dialog
      ref={dialog}
      className="modal position-manager"
      aria-label="Pozisyon detayları"
      onCancel={close}
    >
      <div className="modal-heading">
        <div>
          <span className="eyebrow">POZİSYON</span>
          <h2>{p?.symbol ?? 'Pozisyon detayları'}</h2>
        </div>
        <button className="icon-button" aria-label="Pencereyi kapat" onClick={close}>
          <X size={20} />
        </button>
      </div>
      {info.error && <ErrorNotice message={info.error} retry={info.refresh} />}
      {!p && !info.error && <Loading rows={3} />}
      {p && (
        <>
          <SourceTag source={p.source} changed={p.plan_changed} />
          <div className="position-detail-summary">
            <div>
              <span>{p.closed_at ? 'Durum' : 'Miktar'}</span>
              <strong>{p.closed_at ? 'Kapandı' : `${number(p.quantity, 0)} lot`}</strong>
            </div>
            <div>
              <span>Ortalama maliyet</span>
              <strong>{money(p.avg_cost)}</strong>
            </div>
            <div>
              <span>Vade</span>
              <strong>{horizons.find(([key]) => key === p.horizon)?.[1]}</strong>
            </div>
            <div>
              <span>Kapanan kısmın kâr / zararı</span>
              <strong className={tone(p.realized_pnl)}>{money(p.realized_pnl)}</strong>
            </div>
          </div>
          {!p.closed_at && (
            <div className="position-manage-actions">
              <button className="primary-button" onClick={() => open('SELL')}>
                <ArrowUpRight size={16} />
                Pozisyonu kapat
              </button>
              <button className="secondary-button" onClick={() => open('BUY')}>
                <ArrowDownLeft size={16} />
                Miktar artır
              </button>
              <button className="secondary-button" onClick={() => open('PLAN')}>
                <Settings2 size={16} />
                Planı düzenle
              </button>
              <button
                className="secondary-button"
                onClick={() => {
                  close();
                  ask(
                    `${p.symbol} için ${p.id} kimlikli pozisyonumu kendi kayıtlı vadesine ve güncel risk tercihime göre değerlendir. Kayıtları değiştirmeden takip ve çıkış koşullarını açıkla.`,
                  );
                }}
              >
                <Bot size={16} />
                Asistanla değerlendir
              </button>
            </div>
          )}
          <div className="position-original">
            <h3>İlk plan</h3>
            <p>
              Stop {money(p.initial_plan.stop_loss)} · Hedef {money(p.initial_plan.target)} ·{' '}
              {horizons.find(([key]) => key === p.initial_plan.horizon)?.[1]}
            </p>
            <p>{p.initial_plan.rationale ?? p.initial_plan.note}</p>
          </div>
        </>
      )}
      <h3 className="history-heading">
        <History size={16} />
        Hareketler ve plan geçmişi
      </h3>
      {history.error && <ErrorNotice message={history.error} retry={history.refresh} />}
      {!history.data && !history.error && <Loading rows={2} />}
      {history.data && (
        <>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Tarih</th>
                  <th>İşlem</th>
                  <th>Lot</th>
                  <th>Fiyat</th>
                </tr>
              </thead>
              <tbody>
                {history.data.transactions.map((tx) => (
                  <tr key={tx.id}>
                    <td>{date(tx.created_at, true)}</td>
                    <td>{tx.kind === 'BUY' ? 'Ekleme' : 'Kapatma'}</td>
                    <td>{number(tx.quantity, 0)}</td>
                    <td>{money(tx.price)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {history.data.plans.map((plan, i) => (
            <div className="plan-revision" key={i}>
              <span>{date(plan.created_at, true)}</span>
              <p>
                Stop {money(plan.stop_loss)} · Hedef {money(plan.target)} ·{' '}
                {horizons.find(([key]) => key === plan.horizon)?.[1]}
              </p>
              {plan.note && <p>{plan.note}</p>}
            </div>
          ))}
        </>
      )}
    </dialog>
  );
}

export default function Portfolio({
  data,
  error,
  refresh,
  edit,
  select,
  ask,
  tab,
  setTab,
  recommendations,
  recommendationsError,
  refreshRecommendations,
  focusId,
  clearFocus,
}: {
  data?: PortfolioData;
  error: string;
  refresh: () => void;
  edit: (editor: Editor) => void;
  select: (symbol: string) => void;
  ask: (text: string) => void;
  tab: PortfolioTab;
  setTab: (tab: PortfolioTab) => void;
  recommendations?: { items: Recommendation[] };
  recommendationsError: string;
  refreshRecommendations: () => void;
  focusId?: string;
  clearFocus: () => void;
}) {
  const { date } = useMarketFormat();
  const [source, setSource] = useState<PositionSource | ''>('');
  const [period, setPeriod] = useState('all');
  const [manager, setManager] = useState<string>();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const sourceQuery = source ? `source=${source}` : '';
  const filtered = useResource<PortfolioData>(source ? `/portfolio?${sourceQuery}` : null, 30000);
  const openData = source ? filtered.data : data;
  const openError = source ? filtered.error : error;
  const closed = useResource<{ closed: ClosedPosition[] }>(
    tab === 'closed' ? `/portfolio/closed?${sourceQuery}` : null,
  );
  const [since, setSince] = useState<string>();
  const performance = useResource<Performance>(
    tab === 'performance'
      ? `/portfolio/performance?${sourceQuery}${since ? `&since=${encodeURIComponent(since)}` : ''}`
      : null,
    30000,
  );
  const active = recommendations?.items.filter((r) => r.active && Date.parse(r.expires_at) > now);
  const currentId = focusId ?? manager;
  const manage = (id: string) => setManager(id);
  const accept = (recommendation: Recommendation) => edit({ mode: 'ACCEPT', recommendation });
  const tabs: [PortfolioTab, string, number | undefined][] = [
    ['recommended', 'Önerilenler', active?.length],
    ['open', 'Açık pozisyonlar', data?.positions.length],
    ['closed', 'Kapanan pozisyonlar', undefined],
    ['performance', 'Performans', undefined],
  ];
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">FİKİRDEN SONUCA</span>
          <h1>Portföyüm</h1>
          <p>Günlük önerilerin, pozisyonların ve sonuçların.</p>
        </div>
        <button className="secondary-button" onClick={() => edit({ mode: 'BUY' })}>
          <Plus size={16} />
          Manuel pozisyon ekle
        </button>
      </div>
      <div className="portfolio-tabs" role="tablist" aria-label="Portföy görünümleri">
        {tabs.map(([id, label, count]) => (
          <button
            key={id}
            role="tab"
            id={`tab-${id}`}
            aria-controls={`panel-${id}`}
            aria-selected={tab === id}
            tabIndex={tab === id ? 0 : -1}
            className={tab === id ? 'active' : ''}
            onClick={() => setTab(id)}
            onKeyDown={(event) => {
              const i = tabs.findIndex(([value]) => value === id);
              const next =
                event.key === 'ArrowRight'
                  ? (i + 1) % tabs.length
                  : event.key === 'ArrowLeft'
                    ? (i + tabs.length - 1) % tabs.length
                    : event.key === 'Home'
                      ? 0
                      : event.key === 'End'
                        ? tabs.length - 1
                        : -1;
              if (next >= 0) {
                event.preventDefault();
                setTab(tabs[next][0]);
                document.getElementById(`tab-${tabs[next][0]}`)?.focus();
              }
            }}
          >
            {label}
            {count !== undefined && <span>{count}</span>}
          </button>
        ))}
      </div>
      {tab !== 'recommended' && (
        <div className="portfolio-filter">
          <div className="filter-tabs" aria-label="Pozisyon kaynağı">
            {(
              [
                ['', 'Tümü'],
                ['system', 'Sistem kaynaklı'],
                ['manual', 'Manuel'],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                aria-pressed={source === value}
                className={source === value ? 'active' : ''}
                onClick={() => setSource(value)}
              >
                {label}
              </button>
            ))}
          </div>
          {tab === 'open' && (
            <button
              className="text-button"
              disabled={!openData?.positions.length}
              onClick={() =>
                ask(
                  'Portföyümü incele. Her pozisyonu kendi kayıtlı vadesine ve güncel risk tercihime göre değerlendir; kayıtları değiştirmeden takip ve çıkış koşullarını açıkla.',
                )
              }
            >
              <Bot size={15} />
              Asistanla değerlendir
            </button>
          )}
        </div>
      )}
      <section id={`panel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`}>
        {tab === 'recommended' && (
          <>
            {recommendationsError && (
              <ErrorNotice message={recommendationsError} retry={refreshRecommendations} />
            )}
            {!recommendations && !recommendationsError ? (
              <Loading rows={4} />
            ) : active?.length ? (
              <div className="recommendation-grid">
                {active.map((r) => (
                  <RecommendationCard key={r.id} item={r} accept={accept} openPosition={manage} />
                ))}
              </div>
            ) : (
              !recommendationsError && <RecommendationEmpty ask={ask} />
            )}
          </>
        )}
        {tab === 'open' && (
          <>
            {openError && (
              <ErrorNotice message={openError} retry={source ? filtered.refresh : refresh} />
            )}
            {!openData && !openError && <Loading rows={4} />}
            {openData && (
              <>
                <PortfolioMetrics portfolio={openData} />
                {openData.summary.missing_prices > 0 && (
                  <p className="inline-note">
                    {openData.summary.missing_prices} pozisyonda fiyat bekleniyor; toplamlar henüz
                    hesaplanamıyor.
                  </p>
                )}
                {!openData.positions.length ? (
                  <Empty title="Henüz açık pozisyon yok">
                    Günlük önerilerden birini dahil edebilir veya manuel pozisyon ekleyebilirsin.
                  </Empty>
                ) : (
                  <div className="position-list">
                    {openData.positions.map((p) => (
                      <article
                        className="position-card"
                        key={p.id}
                        aria-label={`${p.symbol} ${p.source === 'system' ? 'sistem' : 'manuel'} pozisyonu`}
                      >
                        <div className="position-top">
                          <button className="symbol-cell" onClick={() => select(p.symbol)}>
                            <SymbolMark symbol={p.symbol} />
                            <span>
                              <strong>{p.symbol}</strong>
                              <small>
                                {number(p.quantity, 0)} lot
                                {p.hold_days !== null ? ` · ${p.hold_days} gündür açık` : ''}
                              </small>
                              <small>
                                Vade: {horizons.find(([key]) => key === p.horizon)?.[1]}
                              </small>
                            </span>
                          </button>
                          <div className="position-pnl">
                            <strong className={tone(p.unrealized_pnl)}>
                              {money(p.unrealized_pnl)}
                            </strong>
                            <small className={tone(p.unrealized_pnl_pct)}>
                              {percent(p.unrealized_pnl_pct)}
                            </small>
                          </div>
                        </div>
                        <SourceTag source={p.source} changed={p.plan_changed} />
                        <div className="position-values">
                          <div>
                            <span>Ortalama maliyet</span>
                            <strong>{money(p.avg_cost)}</strong>
                          </div>
                          <div>
                            <span>{priceLabel(p.price_kind)}</span>
                            <strong>{money(p.current_price)}</strong>
                          </div>
                          <div>
                            <span>Stop</span>
                            <strong>{money(p.stop_loss)}</strong>
                            <small>
                              {p.distance_to_stop_pct !== undefined
                                ? `${percent(p.distance_to_stop_pct)} uzaklık`
                                : 'Belirlenmemiş'}
                            </small>
                          </div>
                          <div>
                            <span>Hedef</span>
                            <strong>{money(p.target)}</strong>
                            <small>
                              {p.distance_to_target_pct !== undefined
                                ? `${percent(p.distance_to_target_pct)} uzaklık`
                                : 'Belirlenmemiş'}
                            </small>
                          </div>
                        </div>
                        <div className="position-attention">
                          {p.current_price != null &&
                            p.stop_loss != null &&
                            p.current_price <= p.stop_loss && (
                              <span className="negative">Stop altında</span>
                            )}
                          {p.current_price != null &&
                            p.target != null &&
                            p.current_price >= p.target && (
                              <span className="positive">Hedef üzerinde</span>
                            )}
                          {p.current_price == null && <span>Fiyat bekleniyor</span>}
                        </div>
                        <div className="position-bottom">
                          <span className="muted">Veri: {date(p.as_of, true)}</span>
                          <button
                            className="secondary-button"
                            aria-label={`${p.symbol} pozisyonunu yönet`}
                            onClick={() => manage(p.id)}
                          >
                            Pozisyonu yönet
                            <ArrowUpRight size={15} />
                          </button>
                        </div>
                      </article>
                    ))}
                  </div>
                )}
              </>
            )}
          </>
        )}
        {tab === 'closed' && (
          <>
            {closed.error && <ErrorNotice message={closed.error} retry={closed.refresh} />}
            {!closed.data && !closed.error && <Loading rows={3} />}
            {closed.data &&
              (closed.data.closed.length ? (
                <div className="closed-position-list">
                  {closed.data.closed.map((p) => (
                    <button className="closed-position-row" key={p.id} onClick={() => manage(p.id)}>
                      <span>
                        <strong>{p.symbol}</strong>
                        <SourceTag source={p.source} />
                        <small>
                          {date(p.closed_at, true)} · {number(p.quantity, 0)} lot
                        </small>
                      </span>
                      <span>
                        <strong className={tone(p.realized_pnl)}>{money(p.realized_pnl)}</strong>
                        <small>{percent(p.realized_pnl_pct)}</small>
                      </span>
                      <ArrowUpRight size={16} />
                    </button>
                  ))}
                </div>
              ) : (
                <Empty title="Henüz kapanan pozisyon yok">
                  Kapattığın pozisyonların sonuçlarını burada görebilirsin.
                </Empty>
              ))}
          </>
        )}
        {tab === 'performance' && (
          <>
            <div className="performance-heading">
              <div>
                <h2>Portföy performansı</h2>
                <p>Fiyat bazlı kâr / zarar</p>
              </div>
              <label>
                Dönem
                <select
                  aria-label="Performans dönemi"
                  value={period}
                  onChange={(e) => {
                    setPeriod(e.target.value);
                    setSince(
                      e.target.value === 'all'
                        ? undefined
                        : new Date(Date.now() - Number(e.target.value) * 86400000).toISOString(),
                    );
                  }}
                >
                  <option value="all">Tüm zamanlar</option>
                  <option value="30">Son 30 gün</option>
                  <option value="90">Son 90 gün</option>
                  <option value="365">Son 1 yıl</option>
                </select>
              </label>
            </div>
            {performance.error && (
              <ErrorNotice message={performance.error} retry={performance.refresh} />
            )}
            {!performance.data && !performance.error && <Loading rows={4} />}
            {performance.data && (
              <>
                <div className="performance-grid">
                  {[
                    [
                      'Kapanan miktarların kâr / zararı',
                      money(performance.data.realized_pnl),
                      'Kısmi kapanışlar dahil',
                    ],
                    [
                      'Kapanan maliyete göre getiri',
                      percent(performance.data.realized_return_pct),
                      money(performance.data.released_cost) + ' maliyet',
                    ],
                    [
                      'Kârlı kapanış oranı',
                      percent(performance.data.win_rate),
                      `${performance.data.closed_count} kapanan pozisyon`,
                    ],
                    [
                      'Ortalama elde tutma',
                      performance.data.average_hold_days === null
                        ? '—'
                        : `${number(performance.data.average_hold_days, 1)} gün`,
                      `${performance.data.known_hold_count} tarihli kayıt`,
                    ],
                  ].map(([label, value, note]) => (
                    <div className="performance-metric" key={label}>
                      <span>{label}</span>
                      <strong>{value}</strong>
                      <small>{note}</small>
                    </div>
                  ))}
                </div>
                <div className="performance-outcomes">
                  <span>
                    <Check size={15} />
                    {performance.data.wins} kârlı
                  </span>
                  <span>{performance.data.losses} zararlı</span>
                  <span>{performance.data.breakeven} başa baş</span>
                </div>
                <h3 className="current-performance-title">Açık pozisyonlar · Güncel durum</h3>
                <PortfolioMetrics
                  portfolio={{ positions: [], summary: performance.data.current }}
                />
                <p className="field-note">
                  Dönem, kapanış tarihine göre uygulanır. Komisyon ve temettü bu hesaba dahil
                  değildir.
                </p>
              </>
            )}
          </>
        )}
      </section>
      {currentId && (
        <PositionManager
          key={currentId}
          id={currentId}
          current={data?.positions.find((p) => p.id === currentId)}
          close={() => {
            setManager(undefined);
            clearFocus();
          }}
          edit={edit}
          ask={ask}
        />
      )}
    </>
  );
}
