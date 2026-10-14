import { Check, Clock3, Globe2, ShieldCheck, SlidersHorizontal } from 'lucide-react';
import type { ConversationPreferences, Horizon, RiskLevel } from '../types';
import { horizons, risks } from '../preferences';
import type { MarketData } from './Markets';
import { ErrorNotice, Loading } from './Common';

const horizonDescriptions: Record<Horizon, string> = {
  intraday: 'Gün içindeki fiyat hareketlerine odaklan.',
  swing: 'Birkaç gün ve haftaya yayılan hareketleri izle.',
  position: 'Daha uzun vadeli eğilimleri değerlendir.',
};
const riskDescriptions: Record<RiskLevel, string> = {
  low: 'Daha sınırlı fiyat dalgalanmalarını tercih ederim.',
  moderate: 'Orta düzeyde fiyat dalgalanmasını kabul ederim.',
  high: 'Yüksek fiyat dalgalanmasını kabul ederim.',
};

export function Preferences({
  value,
  change,
  saving,
  markets,
  marketsError,
  retryMarkets,
  selectedMarkets,
  changeMarkets,
}: {
  value: ConversationPreferences;
  change: (value: ConversationPreferences) => void;
  saving: boolean;
  markets: MarketData | undefined;
  marketsError: string | null;
  retryMarkets: () => void;
  selectedMarkets: string[];
  changeMarkets: (selected: string[]) => void;
}) {
  return (
    <div className="preferences-page">
      <div className="preferences-heading">
        <span className="preferences-emblem">
          <SlidersHorizontal size={24} />
        </span>
        <span className="eyebrow">SANA GÖRE BİR GÖRÜNÜM</span>
        <h1>Tercihler</h1>
        <p>Analiz odağını ve takip ettiğin piyasaları kendine göre düzenle.</p>
      </div>
      <section className="preferences-section" aria-labelledby="trading-heading">
        <div className="preferences-section-heading">
          <div>
            <span className="eyebrow">01 / ANALİZ</span>
            <h2 id="trading-heading">Trading tercihleri</h2>
          </div>
          <span className="preferences-section-icon">
            <SlidersHorizontal size={20} />
          </span>
        </div>
        <fieldset className="preference-group" disabled={saving}>
          <legend>
            <Clock3 size={17} />
            İşlem vadesi
          </legend>
          <p>Hangi zaman aralığına odaklanmak istersin?</p>
          <div className="preference-choices">
            {horizons.map(([key, label], index) => (
              <label
                className={`preference-choice ${value.horizon === key ? 'selected' : ''}`}
                key={key}
              >
                <input
                  type="radio"
                  name="horizon"
                  aria-label={label}
                  checked={value.horizon === key}
                  onChange={() => change({ ...value, horizon: key })}
                />
                <span className="choice-top">
                  <span className="horizon-mark" aria-hidden="true">
                    {['1G', '1H+', '1A+'][index]}
                  </span>
                  <span className="choice-check">
                    <Check size={13} />
                  </span>
                </span>
                <strong>{label}</strong>
                <span className="choice-description">{horizonDescriptions[key]}</span>
              </label>
            ))}
          </div>
          <button
            className="preference-clear"
            disabled={saving || value.horizon === null}
            onClick={() => change({ ...value, horizon: null })}
          >
            {value.horizon === null ? 'Vade belirtilmedi' : 'Vade seçimini kaldır'}
          </button>
        </fieldset>
        <fieldset className="preference-group" disabled={saving}>
          <legend>
            <ShieldCheck size={17} />
            Risk Seviyesi
          </legend>
          <p>Ne kadar fiyat dalgalanmasını kabul edebilirsin?</p>
          <div className="preference-choices">
            {risks.map(([key, label], index) => (
              <label
                className={`preference-choice ${value.risk_level === key ? 'selected' : ''}`}
                key={key}
              >
                <input
                  type="radio"
                  name="risk"
                  aria-label={label}
                  checked={value.risk_level === key}
                  onChange={() => change({ ...value, risk_level: key })}
                />
                <span className="choice-top">
                  <span className="risk-mark" aria-hidden="true">
                    {[0, 1, 2].map((bar) => (
                      <i key={bar} className={bar <= index ? 'filled' : ''} />
                    ))}
                  </span>
                  <span className="choice-check">
                    <Check size={13} />
                  </span>
                </span>
                <strong>{label}</strong>
                <span className="choice-description">{riskDescriptions[key]}</span>
              </label>
            ))}
          </div>
          <button
            className="preference-clear"
            disabled={saving || value.risk_level === null}
            onClick={() => change({ ...value, risk_level: null })}
          >
            {value.risk_level === null ? 'Risk seviyesi belirtilmedi' : 'Risk seçimini kaldır'}
          </button>
        </fieldset>
      </section>
      <section className="preferences-section" aria-labelledby="markets-heading">
        <div className="preferences-section-heading">
          <div>
            <span className="eyebrow">02 / PİYASA ÖZETİ</span>
            <h2 id="markets-heading">Piyasa kartları</h2>
          </div>
          <span className="preferences-section-icon">
            <Globe2 size={21} />
          </span>
        </div>
        <div className="market-selection-heading">
          <p>Çalışma alanında görmek istediğin piyasaları seç.</p>
          <span>{selectedMarkets.length} seçili</span>
        </div>
        {marketsError ? (
          <ErrorNotice message={marketsError} retry={retryMarkets} />
        ) : !markets ? (
          <Loading rows={3} />
        ) : (
          <div className="market-selections">
            {markets.items.map(({ id, name }) => (
              <label
                className={`market-selection ${selectedMarkets.includes(id) ? 'selected' : ''}`}
                key={id}
              >
                <input
                  type="checkbox"
                  aria-label={name}
                  checked={selectedMarkets.includes(id)}
                  onChange={(event) =>
                    changeMarkets(
                      event.target.checked
                        ? [...selectedMarkets, id]
                        : selectedMarkets.filter((value) => value !== id),
                    )
                  }
                />
                <span className="market-selection-symbol" aria-hidden="true">
                  {name.slice(0, 2).toUpperCase()}
                </span>
                <strong>{name}</strong>
                <span className="market-switch" aria-hidden="true" />
              </label>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
