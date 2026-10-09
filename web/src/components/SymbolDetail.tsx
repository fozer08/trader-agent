import { useState } from 'react';
import { ArrowLeft, MessageSquare, Plus } from 'lucide-react';
import { useResource } from '../api';
import type { SymbolDetail as Detail } from '../types';
import { money, number, percent, priceLabel, tone, trend } from '../format';
import { useMarketFormat } from '../market';
import { ErrorNotice, Loading, Metric, NewsPanel, PanelHeading } from './Common';
import PriceChart from './Chart';
import { SymbolMark } from './Watchlist';

export default function SymbolDetail({
  symbol,
  back,
  ask,
  buy,
}: {
  symbol: string;
  back: () => void;
  ask: (message: string) => void;
  buy: (symbol: string) => void;
}) {
  const result = useResource<Detail>(`/symbols/${encodeURIComponent(symbol)}`, 120000);
  const [timeframe, setTimeframe] = useState<'D1' | 'M5' | 'M15'>('D1');
  const item = result.data;
  const { date } = useMarketFormat();
  const indicators =
    timeframe === 'D1' ? item?.daily : item?.intraday[timeframe === 'M5' ? 'm5' : 'm15'];
  return (
    <>
      <button className="text-button back-button" onClick={back}>
        <ArrowLeft size={15} /> Takip listesine dön
      </button>
      <div className="symbol-header">
        <div className="symbol-cell">
          <SymbolMark symbol={symbol} />
          <div>
            <h1>{symbol}</h1>
            <p>{item?.name ?? 'Hisse inceleme'} · BIST</p>
          </div>
        </div>
        <div className="actions">
          <button
            className="secondary-button"
            onClick={() =>
              ask(
                `${symbol} için güncel verileri inceleyip teknik görünümü ve olası işlem koşullarını değerlendir.`,
              )
            }
          >
            <MessageSquare size={16} /> Asistana sor
          </button>
          <button className="primary-button" onClick={() => buy(symbol)}>
            <Plus size={16} /> Alım kaydet
          </button>
        </div>
      </div>
      {result.error && <ErrorNotice message={result.error} retry={result.refresh} />}
      {!item && !result.error ? (
        <Loading rows={3} />
      ) : (
        item && (
          <div className="symbol-price">
            <strong>{money(item.price)}</strong>
            <span className={tone(item.change_pct)}>{percent(item.change_pct)}</span>
            <small>
              {priceLabel(item.price_kind)} · {date(item.as_of, true)}
            </small>
          </div>
        )
      )}
      <section className="panel">
        <PanelHeading
          eyebrow="FİYAT GRAFİĞİ"
          title={timeframe === 'D1' ? 'Günlük görünüm' : 'Gün içi fiyat hareketi'}
        >
          <div className="filter-tabs compact">
            {(
              [
                ['D1', '1 gün'],
                ['M15', '15 dk'],
                ['M5', '5 dk'],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                className={timeframe === value ? 'active' : ''}
                onClick={() => setTimeframe(value)}
              >
                {label}
              </button>
            ))}
          </div>
        </PanelHeading>
        <PriceChart symbol={symbol} timeframe={timeframe} height={370} />
      </section>
      <div className="metrics technical-metrics">
        <Metric label={`Trend · ${timeframe}`} value={trend(indicators?.ema_trend)}>
          EMA’ya göre fiyat konumu
        </Metric>
        <Metric label="RSI" value={number(indicators?.rsi, 1)}>
          Göreceli güç endeksi
        </Metric>
        <Metric label="ATR" value={number(indicators?.atr)}>
          Ortalama gerçek aralık
        </Metric>
        <Metric label="MACD histogram" value={number(indicators?.macd_histogram, 3)}>
          {timeframe === 'D1' ? 'Günlük momentum' : 'Bu aralıkta hesaplanmıyor'}
        </Metric>
      </div>
      {timeframe !== 'D1' && !indicators && (
        <div className="inline-note">Gün içi göstergeler için henüz yeterli veri bulunmuyor.</div>
      )}
      {item && (
        <section className="panel">
          <PanelHeading eyebrow="SON TAMAMLANAN SEANS" title="Referans seviyeleri" />
          <div className="levels-grid">
            {[
              ['Destek 2', item.levels.pivot.s2],
              ['Destek 1', item.levels.pivot.s1],
              ['Pivot', item.levels.pivot.pp],
              ['Direnç 1', item.levels.pivot.r1],
              ['Direnç 2', item.levels.pivot.r2],
            ].map(([label, value]) => (
              <div key={label}>
                <span>{label}</span>
                <strong>{money(value as number)}</strong>
              </div>
            ))}
          </div>
          <div className="level-notes">
            <span>
              Önceki yüksek <b>{number(item.levels.prev_high)}</b>
            </span>
            <span>
              Önceki düşük <b>{number(item.levels.prev_low)}</b>
            </span>
            <span>
              Haftalık aralık{' '}
              <b>
                {number(item.levels.weekly_low)} – {number(item.levels.weekly_high)}
              </b>
            </span>
          </div>
        </section>
      )}
      <NewsPanel symbol={symbol} />
    </>
  );
}
