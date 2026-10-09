import { useEffect, useRef, useState } from 'react';
import { ArrowDownLeft, ArrowUpRight, Check, History, Plus, Settings2, X } from 'lucide-react';
import { request, useResource } from '../api';
import type {
  ClosedPosition,
  Portfolio as PortfolioData,
  Position,
  Stock,
  Transaction,
} from '../types';
import { money, number, percent, priceLabel, tone } from '../format';
import { useMarketFormat } from '../market';
import { Empty, ErrorNotice, Loading, PortfolioMetrics } from './Common';
import { SymbolMark } from './Watchlist';

export interface Editor {
  mode: 'BUY' | 'SELL' | 'PLAN';
  symbol?: string;
  position?: Position;
}

export function TransactionEditor({
  editor,
  symbols,
  close,
  saved,
}: {
  editor: Editor;
  symbols: Stock[];
  close: () => void;
  saved: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const plan = editor.mode === 'PLAN';
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const form = new FormData(event.currentTarget);
    const symbol = String(form.get('symbol') ?? '')
      .trim()
      .toUpperCase();
    const optionalNumber = (key: string) => (form.get(key) ? Number(form.get(key)) : undefined);
    setPending(true);
    setError('');
    try {
      if (plan) {
        await request(`/portfolio/${encodeURIComponent(symbol)}`, {
          method: 'PATCH',
          body: JSON.stringify({
            stop_loss: optionalNumber('stop_loss'),
            target: optionalNumber('target'),
            note: String(form.get('note') ?? ''),
          }),
        });
      } else {
        await request('/portfolio/transactions', {
          method: 'POST',
          body: JSON.stringify({
            symbol,
            kind: editor.mode,
            quantity: Number(form.get('quantity')),
            price: Number(form.get('price')),
            ...(editor.mode === 'BUY'
              ? {
                  stop_loss: optionalNumber('stop_loss'),
                  target: optionalNumber('target'),
                  ...(form.get('note') ? { note: String(form.get('note')) } : {}),
                }
              : {}),
          }),
        });
      }
      saved();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Kayıt tamamlanamadı.');
      setPending(false);
    }
  }
  return (
    <dialog
      ref={dialog}
      className="modal"
      aria-labelledby="editor-title"
      onCancel={(event) => {
        if (pending) event.preventDefault();
        else close();
      }}
    >
      <div className="modal-heading">
        <div>
          <span className="eyebrow">PORTFÖY</span>
          <h2 id="editor-title">
            {plan ? 'Pozisyon planı' : editor.mode === 'BUY' ? 'Alım kaydet' : 'Satış kaydet'}
          </h2>
        </div>
        <button
          className="icon-button"
          aria-label="Pencereyi kapat"
          disabled={pending}
          onClick={close}
        >
          <X size={20} />
        </button>
      </div>
      <p className="modal-description">
        {plan
          ? 'Stop, hedef ve pozisyon notunu güncelle.'
          : 'Gerçekleştirdiğin işlemin lotunu ve fiyatını gir.'}
      </p>
      <form onSubmit={submit}>
        <label>
          Hisse
          <input
            name="symbol"
            aria-label="Hisse sembolü"
            defaultValue={editor.symbol ?? editor.position?.symbol ?? ''}
            list="stocklist-symbols"
            autoComplete="off"
            required
            pattern="[A-Za-z0-9]+"
            maxLength={20}
            readOnly={plan || editor.mode === 'SELL'}
            autoFocus={!editor.symbol}
          />
        </label>
        <datalist id="stocklist-symbols">
          {symbols.map((item) => (
            <option key={item.symbol} value={item.symbol}>
              {item.name}
            </option>
          ))}
        </datalist>
        {!plan && (
          <div className="form-columns">
            <label>
              Lot
              <input
                name="quantity"
                type="number"
                min={1}
                max={editor.mode === 'SELL' ? editor.position?.quantity : undefined}
                step={1}
                required
                defaultValue={editor.mode === 'SELL' ? editor.position?.quantity : undefined}
              />
            </label>
            <label>
              İşlem fiyatı / TL
              <input name="price" type="number" min="0.0001" step="any" required />
            </label>
          </div>
        )}
        {editor.mode === 'SELL' && (
          <p className="field-note">
            Mevcut miktar: {number(editor.position?.quantity, 0)} lot. Tam satışta pozisyon kapanır;
            işlem geçmişi korunur.
          </p>
        )}
        {(plan || editor.mode === 'BUY') && (
          <>
            <div className="form-columns">
              <label>
                Stop / TL
                <input
                  name="stop_loss"
                  type="number"
                  min="0.0001"
                  step="any"
                  defaultValue={editor.position?.stop_loss ?? ''}
                />
              </label>
              <label>
                Hedef / TL
                <input
                  name="target"
                  type="number"
                  min="0.0001"
                  step="any"
                  defaultValue={editor.position?.target ?? ''}
                />
              </label>
            </div>
            {plan && (
              <p className="field-note">Boş bırakılan stop ve hedef mevcut değerini korur.</p>
            )}
            <label>
              Pozisyon notu <span className="muted">· isteğe bağlı</span>
              <textarea
                name="note"
                rows={3}
                maxLength={400}
                defaultValue={editor.position?.note ?? ''}
                placeholder="İşlem gerekçesi veya çıkış koşulu"
              />
            </label>
          </>
        )}
        {error && <ErrorNotice message={error} />}
        <div className="modal-actions">
          <button type="button" className="secondary-button" disabled={pending} onClick={close}>
            Vazgeç
          </button>
          <button className="primary-button" disabled={pending}>
            <Check size={16} />
            {pending
              ? 'Kaydediliyor…'
              : plan
                ? 'Planı güncelle'
                : editor.mode === 'BUY'
                  ? 'Alımı kaydet'
                  : 'Satışı kaydet'}
          </button>
        </div>
      </form>
    </dialog>
  );
}

function HistoryDialog({ symbol, close }: { symbol: string; close: () => void }) {
  const { date } = useMarketFormat();
  const dialog = useRef<HTMLDialogElement>(null);
  const history = useResource<{ transactions: Transaction[] }>(`/portfolio/${symbol}/transactions`);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="modal history-modal"
      aria-labelledby="history-title"
      onCancel={close}
    >
      <div className="modal-heading">
        <div>
          <span className="eyebrow">İŞLEM GEÇMİŞİ</span>
          <h2 id="history-title">{symbol}</h2>
        </div>
        <button className="icon-button" aria-label="Geçmişi kapat" onClick={close}>
          <X size={20} />
        </button>
      </div>
      {history.error && <ErrorNotice message={history.error} retry={history.refresh} />}
      {!history.data && !history.error ? (
        <Loading />
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>İşlem</th>
                <th>Lot</th>
                <th>Fiyat</th>
                <th>Kayıt zamanı</th>
              </tr>
            </thead>
            <tbody>
              {history.data?.transactions.map((tx) => (
                <tr key={tx.id}>
                  <td className={tx.kind === 'BUY' ? 'positive' : 'negative'}>
                    {tx.kind === 'BUY' ? 'Alım' : 'Satış'}
                  </td>
                  <td>{number(tx.quantity, 0)}</td>
                  <td>{money(tx.price)}</td>
                  <td>{date(tx.created_at, true)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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
}: {
  data?: PortfolioData;
  error: string;
  refresh: () => void;
  edit: (editor: Editor) => void;
  select: (symbol: string) => void;
}) {
  const [tab, setTab] = useState('open');
  const { date } = useMarketFormat();
  const [history, setHistory] = useState<string>();
  const closed = useResource<{ closed: ClosedPosition[] }>(
    tab === 'closed' ? '/portfolio/closed' : null,
  );
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">SERMAYENİN GÖRÜNÜMÜ</span>
          <h1>Portföyüm</h1>
          <p>Pozisyonlarını, işlem geçmişini ve çıkış planını takip et.</p>
        </div>
        <button className="primary-button" onClick={() => edit({ mode: 'BUY' })}>
          <Plus size={16} /> Alım kaydet
        </button>
      </div>
      <PortfolioMetrics portfolio={data} />
      {data?.summary.missing_prices ? (
        <div className="inline-note">
          {data.summary.missing_prices} pozisyonda fiyat eksik; toplamlar henüz hesaplanamıyor.
        </div>
      ) : null}
      <section className="panel">
        <div className="table-toolbar">
          <div className="filter-tabs">
            <button className={tab === 'open' ? 'active' : ''} onClick={() => setTab('open')}>
              Açık pozisyonlar <span>{data?.positions.length ?? '—'}</span>
            </button>
            <button className={tab === 'closed' ? 'active' : ''} onClick={() => setTab('closed')}>
              Kapanmış pozisyonlar
            </button>
          </div>
        </div>
        {tab === 'open' ? (
          <>
            {error && <ErrorNotice message={error} retry={refresh} />}
            {!data && !error ? (
              <Loading rows={5} />
            ) : !data?.positions.length ? (
              <Empty title="Portföyün ilk işlemini bekliyor">
                Alım kaydını eklediğinde maliyet, kâr/zarar ve çıkış planın burada görünecek.
              </Empty>
            ) : (
              <div className="position-list">
                {data.positions.map((position) => (
                  <div className="position-card" key={position.symbol}>
                    <div className="position-top">
                      <button className="symbol-cell" onClick={() => select(position.symbol)}>
                        <SymbolMark symbol={position.symbol} />
                        <span>
                          <strong>{position.symbol}</strong>
                          <small>
                            {number(position.quantity, 0)} lot · {position.hold_days} gündür açık
                          </small>
                        </span>
                      </button>
                      <div className="position-pnl">
                        <strong className={tone(position.unrealized_pnl)}>
                          {money(position.unrealized_pnl)}
                        </strong>
                        <small className={tone(position.unrealized_pnl_pct)}>
                          {percent(position.unrealized_pnl_pct)}
                        </small>
                      </div>
                    </div>
                    <div className="position-values">
                      <div>
                        <span>Ortalama maliyet</span>
                        <strong>{money(position.avg_cost)}</strong>
                      </div>
                      <div>
                        <span>{priceLabel(position.price_kind)}</span>
                        <strong>{money(position.current_price)}</strong>
                      </div>
                      <div>
                        <span>Stop seviyesi</span>
                        <strong>{money(position.stop_loss)}</strong>
                        <small
                          className={(position.distance_to_stop_pct ?? 100) < 3 ? 'negative' : ''}
                        >
                          {position.stop_loss
                            ? `${percent(position.distance_to_stop_pct)} uzaklık`
                            : 'Belirlenmemiş'}
                        </small>
                      </div>
                      <div>
                        <span>Hedef seviyesi</span>
                        <strong>{money(position.target)}</strong>
                        <small>
                          {position.target
                            ? `${percent(position.distance_to_target_pct)} uzaklık`
                            : 'Belirlenmemiş'}
                        </small>
                      </div>
                    </div>
                    {position.note && <p className="position-note">{position.note}</p>}
                    <div className="position-bottom">
                      <span className="muted">Veri: {date(position.as_of, true)}</span>
                      <div className="actions">
                        <button className="text-button" onClick={() => setHistory(position.symbol)}>
                          <History size={14} /> Geçmiş
                        </button>
                        <button
                          className="text-button"
                          onClick={() => edit({ mode: 'PLAN', position })}
                        >
                          <Settings2 size={14} /> Planı düzenle
                        </button>
                        <button
                          className="text-button"
                          onClick={() => edit({ mode: 'BUY', symbol: position.symbol, position })}
                        >
                          <ArrowDownLeft size={14} /> Alım ekle
                        </button>
                        <button
                          className="text-button"
                          onClick={() => edit({ mode: 'SELL', symbol: position.symbol, position })}
                        >
                          <ArrowUpRight size={14} /> Satış kaydet
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        ) : (
          <>
            {closed.error && <ErrorNotice message={closed.error} retry={closed.refresh} />}
            {!closed.data && !closed.error ? (
              <Loading />
            ) : !closed.data?.closed.length ? (
              <Empty title="Henüz kapanmış pozisyon yok">
                Lotu sıfıra inen pozisyonlar burada listelenir.
              </Empty>
            ) : (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Hisse</th>
                      <th>Alım / satış ort.</th>
                      <th>Lot</th>
                      <th>Gerçekleşen K/Z</th>
                      <th>Kapanış</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {closed.data.closed.map((position, i) => (
                      <tr key={`${position.symbol}-${i}`}>
                        <td>
                          <strong>{position.symbol}</strong>
                        </td>
                        <td>
                          {money(position.avg_buy_cost)} / {money(position.avg_sell_price)}
                        </td>
                        <td>{number(position.quantity, 0)}</td>
                        <td className={tone(position.realized_pnl)}>
                          {money(position.realized_pnl)}
                          <small>{percent(position.realized_pnl_pct)}</small>
                        </td>
                        <td>{date(position.closed_at)}</td>
                        <td>
                          <button
                            className="icon-button"
                            aria-label={`${position.symbol} işlem geçmişi`}
                            onClick={() => setHistory(position.symbol)}
                          >
                            <History size={16} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </section>
      {history && <HistoryDialog symbol={history} close={() => setHistory(undefined)} />}
    </>
  );
}
