import { useEffect, useRef, useState } from 'react';
import { Check, X } from 'lucide-react';
import { request, useResource } from '../api';
import type { Horizon, Position, Recommendation, Stock, SymbolDetail } from '../types';
import { horizons } from '../preferences';
import { money } from '../format';
import { ErrorNotice } from './Common';

export interface Editor {
  mode: 'BUY' | 'SELL' | 'PLAN' | 'ACCEPT';
  symbol?: string;
  position?: Position;
  recommendation?: Recommendation;
}
interface Preview {
  amount: number;
  planned_loss: number | null;
  planned_gain: number | null;
  realized_pnl: number | null;
}

export function TransactionEditor({
  editor,
  symbols,
  close,
  saved,
  defaultHorizon,
}: {
  editor: Editor;
  symbols: Stock[];
  close: () => void;
  saved: (id?: string) => void;
  defaultHorizon: Horizon | null;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const operation = useRef({ payload: '', id: crypto.randomUUID() });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [quantity, setQuantity] = useState(
    editor.mode === 'SELL' ? String(editor.position?.quantity ?? '') : '',
  );
  const [price, setPrice] = useState('');
  const rec = editor.recommendation;
  const position = editor.position;
  const plan = editor.mode === 'PLAN';
  const adding = editor.mode === 'BUY' && !!position;
  const [stop, setStop] = useState(String(rec?.stop_loss ?? position?.stop_loss ?? ''));
  const [target, setTarget] = useState(String(rec?.target ?? position?.target ?? ''));
  const [preview, setPreview] = useState<Preview>();
  const [previewError, setPreviewError] = useState('');
  const quote = useResource<SymbolDetail>(
    rec ? `/symbols/${rec.symbol}?horizon=${rec.horizon}` : null,
  );
  const minPrice =
    rec?.entry_min ??
    (adding && position?.source === 'system' ? position.initial_plan.entry_min : undefined);
  const maxPrice =
    rec?.entry_max ??
    (adding && position?.source === 'system' ? position.initial_plan.entry_max : undefined);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  useEffect(() => {
    if (
      rec &&
      quote.data?.price != null &&
      quote.data.price >= rec.entry_min &&
      quote.data.price <= rec.entry_max
    )
      setPrice((current) => current || String(quote.data!.price));
  }, [quote.data, rec]);
  useEffect(() => {
    setPreview(undefined);
    setPreviewError('');
    if (plan || !Number.isInteger(Number(quantity)) || Number(quantity) <= 0 || Number(price) <= 0)
      return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void request<Preview>('/portfolio/preview', {
        method: 'POST',
        signal: controller.signal,
        body: JSON.stringify({
          quantity: Number(quantity),
          price: Number(price),
          ...(editor.mode === 'SELL'
            ? { avg_cost: position?.avg_cost }
            : { stop_loss: Number(stop) || undefined, target: Number(target) || undefined }),
        }),
      })
        .then(setPreview)
        .catch((err) => {
          if (!controller.signal.aborted)
            setPreviewError(err instanceof Error ? err.message : 'Tutar hesaplanamadı.');
        });
    }, 180);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [quantity, price, stop, target, plan, editor.mode, position?.avg_cost]);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (pending || !form.current) return;
    setPending(true);
    setError('');
    const values = new FormData(form.current);
    const symbol = String(values.get('symbol') || '')
      .trim()
      .toUpperCase();
    let endpoint = '/portfolio/transactions';
    let payload: Record<string, unknown>;
    if (plan) {
      endpoint = `/portfolio/${position!.id}`;
      payload = {
        stop_loss: Number(stop) || undefined,
        target: Number(target) || undefined,
        horizon: values.get('horizon'),
        note: values.get('note'),
      };
    } else if (rec) {
      endpoint = `/recommendations/${rec.id}/accept`;
      payload = { quantity: Number(quantity), price: Number(price) };
    } else {
      payload = {
        symbol,
        kind: editor.mode,
        quantity: Number(quantity),
        price: Number(price),
        position_id: position?.id,
        ...(!position
          ? {
              horizon: values.get('horizon'),
              stop_loss: Number(stop) || undefined,
              target: Number(target) || undefined,
              note: values.get('note') || undefined,
              opened_at: values.get('opened_at')
                ? new Date(String(values.get('opened_at'))).toISOString()
                : undefined,
            }
          : {}),
      };
    }
    const signature = JSON.stringify([endpoint, payload]);
    if (signature !== operation.current.payload)
      operation.current = { payload: signature, id: crypto.randomUUID() };
    try {
      const result = await request<{ position: Position | null }>(endpoint, {
        method: plan ? 'PATCH' : 'POST',
        body: JSON.stringify({
          ...payload,
          ...(!plan ? { operation_id: operation.current.id } : {}),
        }),
      });
      saved(result.position?.id ?? position?.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Kayıt tamamlanamadı.');
      setPending(false);
    }
  }
  const title = plan
    ? 'Planı düzenle'
    : rec
      ? 'Portföye dahil et'
      : editor.mode === 'SELL'
        ? 'Pozisyonu kapat'
        : adding
          ? 'Miktar artır'
          : 'Manuel pozisyon ekle';
  return (
    <dialog
      ref={dialog}
      className="modal position-editor"
      aria-labelledby="editor-title"
      onCancel={(event) => (pending ? event.preventDefault() : close())}
    >
      <div className="modal-heading">
        <div>
          <span className="eyebrow">
            {rec
              ? 'SİSTEM ÖNERİSİ'
              : position?.source === 'system'
                ? 'SİSTEM KAYNAKLI'
                : 'PORTFÖYÜM'}
          </span>
          <h2 id="editor-title">{title}</h2>
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
      {rec && (
        <div className="entry-plan">
          <strong>{rec.symbol}</strong>
          <span>{horizons.find(([key]) => key === rec.horizon)?.[1]}</span>
          <p>
            Stop {money(rec.stop_loss)} · Hedef {money(rec.target)}
          </p>
        </div>
      )}
      <form ref={form} onSubmit={submit}>
        {!rec && (
          <label>
            Hisse
            <input
              name="symbol"
              aria-label="Hisse sembolü"
              defaultValue={editor.symbol ?? position?.symbol ?? ''}
              list="stocklist-symbols"
              required
              pattern="[A-Za-z0-9]+"
              readOnly={!!position || !!editor.symbol}
              autoFocus={!editor.symbol && !position}
              autoComplete="off"
            />
            <datalist id="stocklist-symbols">
              {symbols.map((s) => (
                <option key={s.symbol} value={s.symbol}>
                  {s.name}
                </option>
              ))}
            </datalist>
          </label>
        )}
        {!plan && (
          <>
            <div className="form-columns">
              <label>
                Lot
                <input
                  name="quantity"
                  aria-label="Lot"
                  type="number"
                  min={1}
                  step={1}
                  max={editor.mode === 'SELL' ? position?.quantity : undefined}
                  required
                  value={quantity}
                  onChange={(e) => setQuantity(e.target.value)}
                  autoFocus={!!rec || !!position}
                />
              </label>
              <label>
                {editor.mode === 'SELL' ? 'Kapatma fiyatı / TL' : 'Giriş fiyatı / TL'}
                <input
                  name="price"
                  aria-label={editor.mode === 'SELL' ? 'Kapatma fiyatı / TL' : 'Giriş fiyatı / TL'}
                  type="number"
                  min={minPrice ?? 0.0001}
                  max={maxPrice}
                  step="any"
                  required
                  value={price}
                  onChange={(e) => setPrice(e.target.value)}
                />
              </label>
            </div>
            {minPrice !== undefined && (
              <p className="price-bounds">
                İzin verilen giriş:{' '}
                <strong>
                  {money(minPrice)} – {money(maxPrice)}
                </strong>
              </p>
            )}
            {editor.mode === 'SELL' && (
              <p className="field-note">
                {position?.quantity} lotun tamamını veya bir kısmını kapatabilirsin.
              </p>
            )}
            {quote.data?.price != null && (
              <p className="field-note">
                Son fiyat: {money(quote.data.price)} · {quote.data.delay_minutes ?? 15} dk gecikmeli
              </p>
            )}
            {quote.error && (
              <ErrorNotice
                message="Güncel fiyat alınamadı. Giriş aralığından fiyat seçebilirsin."
                retry={quote.refresh}
              />
            )}
          </>
        )}
        {(plan || (!rec && !position)) && (
          <>
            <label>
              Pozisyon vadesi
              <select
                name="horizon"
                required
                defaultValue={position?.horizon ?? defaultHorizon ?? ''}
              >
                <option value="" disabled>
                  Vade seç
                </option>
                {horizons.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <details className="position-extra" open={plan}>
              <summary>{plan ? 'Plan bilgileri' : 'Stop, hedef ve diğer bilgiler'}</summary>
              <div className="form-columns">
                <label>
                  Stop / TL
                  <input
                    name="stop_loss"
                    type="number"
                    min="0.0001"
                    step="any"
                    value={stop}
                    onChange={(e) => setStop(e.target.value)}
                  />
                </label>
                <label>
                  Hedef / TL
                  <input
                    name="target"
                    type="number"
                    min="0.0001"
                    step="any"
                    value={target}
                    onChange={(e) => setTarget(e.target.value)}
                  />
                </label>
              </div>
              {!position && (
                <label>
                  Açılış tarihi · isteğe bağlı
                  <input name="opened_at" aria-label="Açılış tarihi" type="datetime-local" />
                </label>
              )}
              <label>
                Pozisyon notu · isteğe bağlı
                <textarea
                  name="note"
                  rows={2}
                  maxLength={400}
                  defaultValue={position?.note ?? ''}
                  placeholder="İşlem gerekçesi veya çıkış koşulu"
                />
              </label>
            </details>
          </>
        )}
        {adding && (
          <p className="field-note">
            Vade: {horizons.find(([key]) => key === position?.horizon)?.[1]}
          </p>
        )}
        {!plan && (
          <div className="entry-preview" aria-live="polite">
            <div>
              <span>Pozisyon tutarı</span>
              <strong>{money(preview?.amount)}</strong>
            </div>
            {editor.mode === 'SELL' ? (
              <div>
                <span>Kapanan kısmın kâr / zararı</span>
                <strong>{money(preview?.realized_pnl)}</strong>
              </div>
            ) : (
              <>
                <div>
                  <span>Stopa göre planlanan kayıp</span>
                  <strong>{money(preview?.planned_loss)}</strong>
                </div>
                <div>
                  <span>Hedefe göre planlanan kazanç</span>
                  <strong>{money(preview?.planned_gain)}</strong>
                </div>
              </>
            )}
          </div>
        )}
        {previewError && <ErrorNotice message={previewError} />}
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
                : editor.mode === 'SELL'
                  ? 'Kapatmayı kaydet'
                  : rec
                    ? 'Portföye dahil et'
                    : 'Pozisyonu kaydet'}
          </button>
        </div>
      </form>
    </dialog>
  );
}
