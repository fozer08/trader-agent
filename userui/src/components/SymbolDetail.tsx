import { useState } from 'react';
import { ArrowLeft, MessageSquare, Plus } from 'lucide-react';
import { useResource } from '../api';
import type { ConversationPreferences, SymbolDetail as Detail } from '../types';
import { money, number, percent, priceLabel, tone, trend } from '../format';
import { useMarketFormat } from '../market';
import { ErrorNotice, Loading, Metric, NewsPanel, PanelHeading } from './Common';
import PriceChart from './Chart';
import { SymbolMark } from './Watchlist';
import { preferenceLabel } from '../preferences';

export default function SymbolDetail({
  symbol,
  back,
  ask,
  buy,
  preferences,
}: {
  symbol: string;
  back: () => void;
  ask: (message: string) => void;
  buy: (symbol: string) => void;
  preferences: ConversationPreferences;
}) {
  const horizon = preferences.horizon ?? 'swing';
  const [timeframe, setTimeframe] = useState<'D1' | 'M5' | 'M15'>('D1');
  const result = useResource<Detail>(
    `/symbols/${encodeURIComponent(symbol)}?horizon=${horizon}`,
    120000,
  );
  const item = result.data?.horizon === horizon ? result.data : undefined;
  const daily = item?.daily;
  const { date } = useMarketFormat();
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
            onClick={() => {
              ask(
                `${symbol} için güncel verileri inceleyip fırsatları ve somut işlem koşullarını değerlendir.`,
              );
            }}
          >
            <MessageSquare size={16} /> Asistana sor
          </button>
          <button className="primary-button" onClick={() => buy(symbol)}>
            <Plus size={16} /> Alım kaydet
          </button>
        </div>
      </div>
      <div className="analysis-preferences">
        <span>{preferenceLabel(preferences)}</span>
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
      {item?.daily_stale && (
        <div className="inline-note">Günlük veri son tamamlanan seansın gerisinde.</div>
      )}
      {item?.session_error && (
        <div className="inline-note">Seans verisi alınamadı; günlük analiz gösteriliyor.</div>
      )}
      {horizon === 'intraday' && (
        <div className="inline-note">
          Seans içi plan için güncel tetik teyidi gerekir. {item?.delay_minutes ?? '—'} dk gecikmeli
          fiyatların gözlenen aralığı, anlık işlem teyidi sağlamaz.
        </div>
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
      <div className={`metrics technical-metrics risk-${preferences.risk_level ?? 'moderate'}`}>
        <Metric label="Günlük fiyat konumu" value={trend(daily?.price_vs_ema)}>
          EMA {daily?.ema_fast_period ?? '—'} · son kapanış
        </Metric>
        <Metric label="RSI · D1" value={number(daily?.rsi, 1)}>
          14 seans · momentum bağlamı
        </Metric>
        <Metric label="ATR · D1" value={number(daily?.atr)}>
          14 seans · {number(daily?.atr_pct)}% oynaklık
        </Metric>
        <Metric
          label={`${horizon === 'intraday' ? 5 : horizon === 'position' ? 60 : 20} seans getirisi`}
          value={percent(
            horizon === 'intraday'
              ? daily?.return_5_pct
              : horizon === 'position'
                ? daily?.return_60_pct
                : daily?.return_20_pct,
          )}
        >
          Dönem performansı
        </Metric>
        {preferences.risk_level === 'high' && (
          <Metric label="Göreli hacim" value={number(daily?.relative_volume)}>
            20 seans ortalamasına göre
          </Metric>
        )}
      </div>
      {daily && Object.keys(daily.unavailable).length > 0 && (
        <div className="inline-note">Bazı göstergeler için yeterli veri bulunmuyor.</div>
      )}
      {daily && (
        <section className="panel">
          <PanelHeading
            eyebrow={`GÜNLÜK REFERANS · ${date(daily.as_of)}`}
            title="Gözlenen fiyat aralıkları"
          />
          <div className="levels-grid">
            {(
              [
                ['Son seans', daily.levels.last_low, daily.levels.last_high],
                ['Son 5 seans', daily.levels.rolling_5_low, daily.levels.rolling_5_high],
                ['Son 20 seans', daily.levels.rolling_20_low, daily.levels.rolling_20_high],
                ['Son 60 seans', daily.levels.rolling_60_low, daily.levels.rolling_60_high],
              ] as const
            ).map(([label, low, high]) => (
              <div key={label}>
                <span>{label}</span>
                <strong>
                  {number(low)} – {number(high)}
                </strong>
              </div>
            ))}
          </div>
          <div className="level-notes">
            <span>
              Son kapanış <b>{money(daily.levels.last_close)}</b>
            </span>
            <span>
              Göreli hacim <b>{number(daily.relative_volume)}×</b> · önceki 20 seansa göre
            </span>
            <span>Aralıklar otomatik destek/direnç veya işlem sinyali değildir.</span>
          </div>
        </section>
      )}
      <NewsPanel symbol={symbol} />
    </>
  );
}
