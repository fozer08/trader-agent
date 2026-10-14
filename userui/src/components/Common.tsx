import { AlertCircle, ArrowUpRight, Inbox, RefreshCw } from 'lucide-react';
import type { ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { News, Portfolio } from '../types';
import { useResource } from '../api';
import { money, number, percent, tone } from '../format';
import { useMarketFormat } from '../market';

export function Markdown({ children }: { children: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}
export function ErrorNotice({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="error-notice" role="alert">
      <AlertCircle size={17} />
      <span>{message}</span>
      {retry && <button onClick={retry}>Tekrar dene</button>}
    </div>
  );
}
export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <Inbox size={29} />
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
export function Loading({ rows = 4 }: { rows?: number }) {
  return (
    <div className="skeletons" aria-label="Yükleniyor" role="status">
      {Array.from({ length: rows }, (_, i) => (
        <div className="skeleton" key={i} style={{ width: `${100 - (i % 3) * 12}%` }} />
      ))}
    </div>
  );
}
export function PanelHeading({
  eyebrow,
  title,
  children,
}: {
  eyebrow?: string;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="panel-heading">
      <div>
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h2>{title}</h2>
      </div>
      {children}
    </div>
  );
}
export function Metric({
  label,
  value,
  children,
  highlight,
}: {
  label: string;
  value: string;
  children?: ReactNode;
  highlight?: boolean;
}) {
  return (
    <div className={`metric ${highlight ? 'highlight' : ''}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <div className="metric-note">{children}</div>
    </div>
  );
}
export function PortfolioMetrics({ portfolio }: { portfolio?: Portfolio }) {
  const summary = portfolio?.summary;
  return (
    <div className="metrics">
      <Metric
        label="Portföy değeri"
        value={portfolio ? money(summary?.market_value) : '—'}
        highlight
      >
        {portfolio?.positions.length ?? '—'} açık pozisyon
      </Metric>
      <Metric label="Açık kâr / zarar" value={money(summary?.unrealized_pnl)}>
        <span className={tone(summary?.unrealized_pnl_pct)}>
          {percent(summary?.unrealized_pnl_pct)}
        </span>{' '}
        maliyete göre
      </Metric>
      <Metric label="Toplam maliyet" value={money(summary?.total_cost)}>
        Açık pozisyonların maliyeti
      </Metric>
      <Metric label="Stoplara brüt mesafe" value={money(summary?.planned_downside_to_stops)}>
        {summary?.breached_stops
          ? `${summary.breached_stops} stop seviyesi aşılmış`
          : summary?.missing_stops
            ? `${summary.missing_stops} pozisyonda stop yok`
            : summary?.missing_prices
              ? 'Eksik fiyat verisi'
              : `En büyük pozisyon: ${number(summary?.largest_position_weight_pct)}%`}
      </Metric>
    </div>
  );
}
export function NewsPanel({ symbol }: { symbol?: string }) {
  const { date } = useMarketFormat();
  const news = useResource<News>(`/news${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ''}`);
  const [latest, ...older] = news.data?.digests ?? [];
  return (
    <section className="panel news-panel">
      <PanelHeading eyebrow="GÜNDEM" title={symbol ? `${symbol} haberleri` : 'Piyasadan notlar'}>
        <button className="icon-button" aria-label="Haberleri yenile" onClick={news.refresh}>
          <RefreshCw size={16} />
        </button>
      </PanelHeading>
      {news.error && <ErrorNotice message={news.error} retry={news.refresh} />}
      {!news.data && !news.error ? (
        <Loading rows={3} />
      ) : latest ? (
        <>
          <div className="news-meta">
            <span>{date(latest.date)}</span>
            <span>{latest.item_count} haberden derlendi</span>
          </div>
          <Markdown>{latest.summary}</Markdown>
          <div className="news-sources">
            {latest.sources.map((source) => (
              <span className="tag" key={source}>
                {source}
              </span>
            ))}
          </div>
          {older.map((item) => (
            <details className="older-news" key={item.date}>
              <summary>
                {date(item.date)} özeti <ArrowUpRight size={14} />
              </summary>
              <Markdown>{item.summary}</Markdown>
            </details>
          ))}
        </>
      ) : (
        <Empty title="Henüz haber özeti yok">
          Haber yenileme işi çalıştığında günlük özet burada görünecek.
        </Empty>
      )}
    </section>
  );
}
