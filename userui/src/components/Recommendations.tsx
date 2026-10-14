import { useEffect, useState } from 'react';
import { ArrowUpRight, Bot, Clock3, Sparkles } from 'lucide-react';
import type { Recommendation } from '../types';
import { money } from '../format';
import { horizons } from '../preferences';
import { useResource } from '../api';
import { ErrorNotice, Loading } from './Common';
import { useMarketFormat } from '../market';

export function RecommendationCard({
  item,
  accept,
  openPosition,
}: {
  item: Recommendation;
  accept: (item: Recommendation) => void;
  openPosition: (id: string) => void;
}) {
  const { date } = useMarketFormat();
  const [expanded, setExpanded] = useState(false);
  const [expired, setExpired] = useState(Date.now() >= Date.parse(item.expires_at));
  useEffect(() => {
    const remaining = Date.parse(item.expires_at) - Date.now();
    setExpired(remaining <= 0);
    const timer = window.setTimeout(() => setExpired(true), Math.max(0, remaining));
    return () => window.clearTimeout(timer);
  }, [item.expires_at]);
  const active = item.active && !expired;
  return (
    <article
      className={`recommendation-card ${!active ? 'expired' : ''}`}
      aria-label={`${item.symbol} önerisi`}
    >
      <div className="recommendation-top">
        <div>
          <span className="source-tag">
            <Sparkles size={12} /> {item.producer === 'assistant' ? 'Asistan' : 'Model'}
          </span>
          <h3>{item.symbol}</h3>
        </div>
        <span className="horizon-tag">{horizons.find(([key]) => key === item.horizon)?.[1]}</span>
      </div>
      <div className="recommendation-entry">
        <span>Giriş aralığı</span>
        <strong>
          {money(item.entry_min)} <span>–</span> {money(item.entry_max)}
        </strong>
      </div>
      <div className="recommendation-levels">
        <div>
          <span>Stop</span>
          <strong>{money(item.stop_loss)}</strong>
        </div>
        <div>
          <span>Hedef</span>
          <strong>{money(item.target)}</strong>
        </div>
      </div>
      <p>{item.rationale}</p>
      {expanded && (
        <div className="recommendation-details">
          <span>Oluşturuldu: {date(item.created_at, true)}</span>
          <span>Veri zamanı: {date(item.data_as_of, true)}</span>
          <span>Son ekleme: {date(item.expires_at, true)}</span>
        </div>
      )}
      <div className="recommendation-actions">
        <button
          className="primary-button"
          disabled={!item.position_id && !active}
          onClick={() => (item.position_id ? openPosition(item.position_id) : accept(item))}
        >
          {item.position_id ? 'Pozisyonu aç' : active ? 'Portföye dahil et' : 'Süresi doldu'}
          <ArrowUpRight size={15} />
        </button>
        <button
          className="text-button"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? 'Kapat' : 'İncele'}
        </button>
      </div>
      <small className="recommendation-validity">
        <Clock3 size={12} />
        {active ? `Bugün · ${date(item.expires_at, true)}` : 'Yeni eklemeye kapalı'}
      </small>
    </article>
  );
}

export function ChatRecommendation({
  id,
  accept,
  openPosition,
}: {
  id: string;
  accept: (item: Recommendation) => void;
  openPosition: (id: string) => void;
}) {
  const resource = useResource<Recommendation>(`/recommendations/${encodeURIComponent(id)}`, 30000);
  return resource.error ? (
    <ErrorNotice message={resource.error} retry={resource.refresh} />
  ) : resource.data ? (
    <RecommendationCard item={resource.data} accept={accept} openPosition={openPosition} />
  ) : (
    <Loading rows={2} />
  );
}

export function RecommendationEmpty({ ask }: { ask: (text: string) => void }) {
  return (
    <div className="recommendation-empty">
      <span className="preferences-emblem">
        <Sparkles size={24} />
      </span>
      <h3>Bugün için henüz öneri yok</h3>
      <p>Takip ettiğin hisseleri asistanla değerlendirebilirsin.</p>
      <button
        className="secondary-button"
        onClick={() =>
          ask(
            'Takip listemi vade ve risk tercihlerime göre değerlendir. Veriler uygunsa giriş aralığı, stop ve hedef içeren somut öneriler oluştur.',
          )
        }
      >
        <Bot size={16} />
        Takip listemi değerlendir
      </button>
    </div>
  );
}
