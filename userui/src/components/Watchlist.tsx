import { useMemo, useState } from 'react';
import { ArrowDownUp, ChevronRight, Plus, Search, Trash2 } from 'lucide-react';
import type { Quote, Stocklist, Watchlist as WatchlistData } from '../types';
import { request, useResource } from '../api';
import { number, percent, priceLabel, tone, trend } from '../format';
import { useMarketFormat } from '../market';
import { Empty, ErrorNotice, Loading } from './Common';

export function SymbolMark({ symbol }: { symbol: string }) {
  return (
    <span className={`symbol-mark mark-${symbol.charCodeAt(0) % 4}`}>{symbol.slice(0, 2)}</span>
  );
}
export default function Watchlist({
  data,
  error,
  refresh,
  select,
  changed,
}: {
  data?: WatchlistData;
  error: string;
  refresh: () => void;
  select: (symbol: string) => void;
  changed: () => void;
}) {
  const stocklist = useResource<Stocklist>('/stocklist');
  const { time } = useMarketFormat();
  const [selected, setSelected] = useState('');
  const [pending, setPending] = useState(false);
  const [mutationError, setMutationError] = useState('');
  const available = (stocklist.data?.items ?? []).filter(
    (stock) => !data?.items.some((item) => item.symbol === stock.symbol),
  );
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [sort, setSort] = useState<'symbol' | 'change_pct' | 'rsi'>('symbol');
  const [descending, setDescending] = useState(false);
  const items = useMemo(() => {
    const filtered = (data?.items ?? []).filter((item) => {
      const matches = `${item.symbol} ${item.name}`
        .toLocaleLowerCase('tr-TR')
        .includes(query.toLocaleLowerCase('tr-TR'));
      return (
        matches &&
        (filter === 'all' ||
          (filter === 'up'
            ? item.daily?.price_vs_ema === 'above'
            : item.daily?.price_vs_ema === 'below'))
      );
    });
    const value = (item: Quote) => (sort === 'rsi' ? item.daily?.rsi : item.change_pct);
    return filtered.sort((a, b) => {
      if (sort === 'symbol') return a.symbol.localeCompare(b.symbol) * (descending ? -1 : 1);
      const av = value(a),
        bv = value(b);
      if (av == null) return bv == null ? 0 : 1;
      if (bv == null) return -1;
      return (av - bv) * (descending ? -1 : 1);
    });
  }, [data, query, filter, sort, descending]);
  function order(next: typeof sort) {
    setDescending(sort === next ? !descending : next !== 'symbol');
    setSort(next);
  }
  async function update(symbol: string, remove = false) {
    if (pending) return;
    setPending(true);
    setMutationError('');
    try {
      await request(remove ? `/watchlist/${encodeURIComponent(symbol)}` : '/watchlist', {
        method: remove ? 'DELETE' : 'POST',
        ...(remove ? {} : { body: JSON.stringify({ symbol }) }),
      });
      setSelected('');
      changed();
    } catch (err) {
      setMutationError(err instanceof Error ? err.message : 'Takip listesi güncellenemedi.');
    } finally {
      setPending(false);
    }
  }
  return (
    <section className="panel watchlist-panel">
      <form
        className="watchlist-manager"
        onSubmit={(event) => {
          event.preventDefault();
          if (selected && available.some((stock) => stock.symbol === selected))
            void update(selected);
        }}
      >
        <label>
          Takip edilecek hisse
          <select
            aria-label="Takip edilecek hisse"
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
            disabled={pending || stocklist.loading || !data || data.items.length >= 10}
            required
          >
            <option value="">{stocklist.loading ? 'Hisseler yükleniyor…' : 'Hisse seçin'}</option>
            {available.map((stock) => (
              <option key={stock.symbol} value={stock.symbol}>
                {stock.symbol} · {stock.name}
              </option>
            ))}
          </select>
        </label>
        <button
          className="secondary-button"
          disabled={pending || !selected || (data?.items.length ?? 0) >= 10}
        >
          <Plus size={16} /> Hisse ekle
        </button>
      </form>
      <p className="watchlist-count">{data?.items.length ?? 0} / 10 hisse</p>
      {stocklist.error && <ErrorNotice message={stocklist.error} retry={stocklist.refresh} />}
      {mutationError && <ErrorNotice message={mutationError} />}
      <div className="table-toolbar">
        <div className="filter-tabs" aria-label="Trend filtresi">
          {[
            ['all', 'Tüm hisseler'],
            ['up', 'EMA üstünde'],
            ['down', 'EMA altında'],
          ].map(([value, label]) => (
            <button
              className={filter === value ? 'active' : ''}
              key={value}
              onClick={() => setFilter(value)}
            >
              {label}
            </button>
          ))}
        </div>
        <label className="search-box">
          <Search size={16} />
          <input
            aria-label="Hisse ara"
            placeholder="Hisse veya şirket ara"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
      </div>
      {error && <ErrorNotice message={error} retry={refresh} />}
      {!data && !error ? (
        <Loading rows={8} />
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>
                  <button onClick={() => order('symbol')}>
                    Hisse <ArrowDownUp size={12} />
                  </button>
                </th>
                <th>Fiyat / TL</th>
                <th>
                  <button onClick={() => order('change_pct')}>
                    Değişim <ArrowDownUp size={12} />
                  </button>
                </th>
                <th>
                  EMA{data?.items.find((item) => item.daily)?.daily?.ema_fast_period ?? '—'} konumu
                </th>
                <th>
                  <button onClick={() => order('rsi')}>
                    RSI <ArrowDownUp size={12} />
                  </button>
                </th>
                <th>D1 hacim</th>
                <th>Veri zamanı</th>
                <th aria-label="Detay" />
                <th aria-label="Takipten çıkar" />
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.symbol}>
                  <td>
                    <button className="symbol-cell" onClick={() => select(item.symbol)}>
                      <SymbolMark symbol={item.symbol} />
                      <span>
                        <strong>{item.symbol}</strong>
                        <small>{item.name}</small>
                      </span>
                    </button>
                  </td>
                  <td className="numeric">
                    {item.pending ? <span className="mini-skeleton" /> : number(item.price)}
                  </td>
                  <td className={`numeric ${tone(item.change_pct)}`}>{percent(item.change_pct)}</td>
                  <td>
                    <span
                      className={`trend-tag ${item.daily?.price_vs_ema === 'above' ? 'up' : item.daily?.price_vs_ema === 'below' ? 'down' : ''}`}
                    >
                      {item.daily ? trend(item.daily.price_vs_ema) : '—'}
                    </span>
                  </td>
                  <td className="numeric">{number(item.daily?.rsi, 1)}</td>
                  <td className="numeric">
                    {item.daily?.relative_volume == null
                      ? '—'
                      : `${number(item.daily.relative_volume)}×`}
                  </td>
                  <td className="quote-time">
                    {item.error ? (
                      <span className="negative" title={item.error}>
                        Veri eksik
                      </span>
                    ) : item.pending ? (
                      'Bekleniyor'
                    ) : (
                      <span title={item.as_of ?? undefined}>
                        {time(item.as_of)}
                        <small>{priceLabel(item.price_kind)}</small>
                      </span>
                    )}
                  </td>
                  <td>
                    <button
                      className="icon-button"
                      aria-label={`${item.symbol} detaylarını aç`}
                      onClick={() => select(item.symbol)}
                    >
                      <ChevronRight size={17} />
                    </button>
                  </td>
                  <td>
                    <button
                      className="icon-button"
                      aria-label={`${item.symbol} takipten çıkar`}
                      title="Takipten çıkar"
                      disabled={pending}
                      onClick={() => void update(item.symbol, true)}
                    >
                      <Trash2 size={16} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!items.length && (
            <Empty title={data?.items.length ? 'Eşleşen hisse yok' : 'Takip listesi boş'}>
              {data?.items.length
                ? 'Aramayı veya trend filtresini değiştirin.'
                : 'Takip etmek istediğiniz hisseleri yukarıdaki listeden ekleyin.'}
            </Empty>
          )}
        </div>
      )}
      <div className="table-footer">
        <span>
          {items.length} hisse <span className="muted">/ {data?.items.length ?? '—'} takipte</span>
        </span>
        <span>
          {data?.refreshing
            ? 'Veriler sırayla yenileniyor…'
            : 'Günlük göstergeler son tamamlanan seansa ait'}
        </span>
      </div>
    </section>
  );
}
