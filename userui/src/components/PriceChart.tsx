import { useEffect, useRef } from 'react';
import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  createChart,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';
import { useResource } from '../api';
import type { Bars } from '../types';
import { ErrorNotice, Empty, Loading } from './Common';
import { useMarket } from '../market';

export default function PriceChart({
  symbol,
  timeframe = 'D1',
  height = 320,
}: {
  symbol: string;
  timeframe?: 'D1' | 'M5' | 'M15';
  height?: number;
}) {
  const container = useRef<HTMLDivElement>(null);
  const meta = useMarket();
  const timezone = meta?.timezone ?? 'Europe/Istanbul';
  const result = useResource<Bars>(
    `/symbols/${encodeURIComponent(symbol)}/bars?timeframe=${timeframe}`,
    120000,
  );
  useEffect(() => {
    if (!container.current || !result.data?.bars.length) return;
    const chart = createChart(container.current, {
      height,
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#11171e' },
        textColor: '#7d8999',
        fontFamily: 'Arial',
        attributionLogo: true,
      },
      grid: { vertLines: { color: '#1c242e' }, horzLines: { color: '#1c242e' } },
      rightPriceScale: { borderColor: '#27313e' },
      timeScale: {
        borderColor: '#27313e',
        timeVisible: timeframe !== 'D1',
        tickMarkFormatter: (value: Time) =>
          typeof value === 'number'
            ? new Intl.DateTimeFormat('tr-TR', {
                timeZone: timezone,
                hour: '2-digit',
                minute: '2-digit',
              }).format(new Date(value * 1000))
            : null,
      },
      localization: {
        locale: 'tr-TR',
        timeFormatter: (value: Time) =>
          typeof value === 'number'
            ? new Intl.DateTimeFormat('tr-TR', {
                timeZone: timezone,
                day: '2-digit',
                month: 'short',
                ...(timeframe !== 'D1' ? ({ hour: '2-digit', minute: '2-digit' } as const) : {}),
              }).format(new Date(value * 1000))
            : typeof value === 'string'
              ? value
              : `${value.day}.${value.month}.${value.year}`,
      },
    });
    const bars = result.data.bars;
    if (result.data.chart_type === 'candlestick') {
      chart
        .addSeries(CandlestickSeries, {
          upColor: '#b6ef67',
          downColor: '#f27b82',
          borderVisible: false,
          wickUpColor: '#b6ef67',
          wickDownColor: '#f27b82',
        })
        .setData(
          bars.map((bar) => ({
            time: bar.time.slice(0, 10),
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close,
          })),
        );
    } else {
      chart
        .addSeries(AreaSeries, {
          lineColor: '#b6ef67',
          topColor: '#b6ef6733',
          bottomColor: '#b6ef6700',
          lineWidth: 2,
        })
        .setData(
          bars.map((bar) => ({
            time:
              timeframe === 'D1'
                ? bar.time.slice(0, 10)
                : (Math.floor(new Date(bar.time).getTime() / 1000) as UTCTimestamp),
            value: bar.close,
          })),
        );
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [result.data, height, timeframe, timezone]);
  return (
    <div className="chart-wrap">
      {result.error ? (
        <ErrorNotice message={result.error} retry={result.refresh} />
      ) : !result.data ? (
        <Loading rows={5} />
      ) : !result.data.bars.length ? (
        <Empty title="Bu aralıkta veri yok">
          {meta?.day_kind === 'closed'
            ? `Bugün seans yok: ${meta.reason}. `
            : meta?.day_kind === 'unknown'
              ? 'Güncel seans için takvim kapsamı eksik. '
              : meta?.day_kind === 'half_day'
                ? `Yarım gün seansı ${meta.session_end} saatinde biter. `
                : 'Gün içi veriler sağlayıcının gecikmesiyle gelir. '}
          Günlük grafiği inceleyebilirsiniz.
        </Empty>
      ) : null}
      <div ref={container} style={{ height: result.data?.bars.length ? height : 0 }} />
      <div className="chart-caption">
        <span>
          {timeframe === 'D1'
            ? result.data?.chart_type === 'candlestick'
              ? 'Günlük açılış, kapanış ve fiyat aralığı'
              : 'Tamamlanmış seanslar · Kapanış fiyatları'
            : `${timeframe.slice(1)} dakikalık fiyat örnekleri · Hacim verisi yok`}
        </span>
        <a
          href="https://www.tradingview.com/"
          target="_blank"
          rel="noreferrer"
          title="TradingView Lightweight Charts™ · Copyright (с) 2025 TradingView, Inc."
        >
          Grafikler: TradingView
        </a>
      </div>
    </div>
  );
}
