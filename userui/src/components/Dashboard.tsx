import { useState } from 'react';
import { ArrowRight, ArrowUpRight, Radar, ShieldCheck } from 'lucide-react';
import type { Portfolio, Watchlist } from '../types';
import { money, percent, tone, trend } from '../format';
import { Empty, NewsPanel, PanelHeading, PortfolioMetrics } from './Common';
import PriceChart from './Chart';
import { SymbolMark } from './Watchlist';

export default function Dashboard({
  watchlist,
  portfolio,
  select,
  ask,
  showPortfolio,
}: {
  watchlist?: Watchlist;
  portfolio?: Portfolio;
  select: (symbol: string) => void;
  ask: (message: string) => void;
  showPortfolio: () => void;
}) {
  const [selected, setFeatured] = useState('');
  const featured =
    watchlist?.items.find((item) => item.symbol === selected)?.symbol ??
    watchlist?.items[0]?.symbol;
  const ready = (watchlist?.items ?? []).filter((item) => item.price != null && !item.error);
  const movers = [...ready]
    .sort((a, b) => (b.change_pct ?? -Infinity) - (a.change_pct ?? -Infinity))
    .slice(0, 5);
  const rising = ready.filter((item) => item.daily?.price_vs_ema === 'above').length;
  const falling = ready.filter((item) => item.daily?.price_vs_ema === 'below').length;
  const attention =
    portfolio?.positions.filter(
      (position) =>
        position.current_price == null ||
        position.stop_loss == null ||
        (position.distance_to_stop_pct ?? Infinity) < 3,
    ) ?? [];
  return (
    <>
      <section className="welcome">
        <div>
          <span className="eyebrow">DAHA NET BİR PİYASA GÖRÜNÜMÜ</span>
          <h1>
            Önce veriye bak.
            <br />
            <span>Sonra karar ver.</span>
          </h1>
          <p>Takip ettiğin hisseler, pozisyonların ve analiz asistanın aynı çalışma alanında.</p>
          <button
            className="primary-button"
            onClick={() =>
              ask(
                'Takip listemi tara. Teknik görünümü karşılaştır ve incelemeye değer hisseleri gerekçeleriyle açıkla.',
              )
            }
          >
            <Radar size={16} /> Takip listemi değerlendir <ArrowUpRight size={16} />
          </button>
        </div>
        <div className="market-breadth">
          <span className="eyebrow">TAKİP LİSTESİ · GÜNLÜK</span>
          <div>
            <strong className="positive">{rising}</strong>
            <span>EMA{ready[0]?.daily?.ema_fast_period ?? '—'} üstünde</span>
          </div>
          <div>
            <strong className="negative">{falling}</strong>
            <span>EMA{ready[0]?.daily?.ema_fast_period ?? '—'} altında</span>
          </div>
          <div className="breadth-bar">
            <span style={{ width: `${ready.length ? (rising / ready.length) * 100 : 0}%` }} />
          </div>
          <small>
            {ready.length} / {watchlist?.items.length ?? '—'} hisse verisi hazır
          </small>
        </div>
      </section>
      <PortfolioMetrics portfolio={portfolio} />
      {portfolio?.summary.missing_prices ? (
        <div className="inline-note">
          {portfolio.summary.missing_prices} pozisyonun fiyatı alınamadı; toplam değer ve kâr/zarar
          tamamlanınca gösterilecek.
        </div>
      ) : null}
      <div className="dashboard-columns">
        <section className="panel">
          <PanelHeading eyebrow="FİYAT HAREKETİ" title={featured ?? 'Takip listene hisse ekle'}>
            <div className="filter-tabs compact">
              {(watchlist?.items ?? []).slice(0, 5).map(({ symbol }) => (
                <button
                  className={featured === symbol ? 'active' : ''}
                  key={symbol}
                  onClick={() => setFeatured(symbol)}
                >
                  {symbol}
                </button>
              ))}
            </div>
          </PanelHeading>
          {featured && <PriceChart symbol={featured} height={280} />}
          <button
            className="text-button chart-detail"
            disabled={!featured}
            onClick={() => featured && select(featured)}
          >
            Hisseyi incele <ArrowRight size={14} />
          </button>
        </section>
        <section className="panel movers">
          <PanelHeading eyebrow="TAKİP LİSTESİ" title="Güçlü fiyat hareketleri" />
          {!movers.length ? (
            <Empty title="Veriler hazırlanıyor">
              Hisseler sağlayıcının istek sınırına göre sırayla yükleniyor.
            </Empty>
          ) : (
            movers.map((item) => (
              <button className="mover-row" key={item.symbol} onClick={() => select(item.symbol)}>
                <SymbolMark symbol={item.symbol} />
                <span>
                  <strong>{item.symbol}</strong>
                  <small>{trend(item.daily?.price_vs_ema)} · D1</small>
                </span>
                <span className={tone(item.change_pct)}>{percent(item.change_pct)}</span>
              </button>
            ))
          )}
        </section>
      </div>
      <div className="dashboard-columns lower">
        <NewsPanel />
        <section className="panel risk-panel">
          <PanelHeading eyebrow="POZİSYON TAKİBİ" title="Risk radarın">
            <ShieldCheck size={20} />
          </PanelHeading>
          {!portfolio?.positions.length ? (
            <Empty title="Henüz açık pozisyon yok">
              İlk işlemini portföye kaydederek maliyet, stop ve hedeflerini takip et.
            </Empty>
          ) : !attention.length ? (
            <Empty title="Yakın stop veya eksik stop yok">
              Mevcut fiyatlara göre stop uzaklıkları %3’ün üzerinde.
            </Empty>
          ) : (
            attention.map((position) => (
              <button
                className="risk-row"
                key={position.symbol}
                onClick={() => select(position.symbol)}
              >
                <strong>{position.symbol}</strong>
                <span>
                  {position.current_price == null
                    ? 'Fiyat alınamadı'
                    : position.stop_loss == null
                      ? 'Stop belirlenmemiş'
                      : `Stop uzaklığı ${percent(position.distance_to_stop_pct)}`}
                </span>
                <small>{money(position.current_price)}</small>
              </button>
            ))
          )}
          <button className="text-button" onClick={showPortfolio}>
            Portföyü aç <ArrowRight size={14} />
          </button>
        </section>
      </div>
    </>
  );
}
