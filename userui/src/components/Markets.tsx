import { ErrorNotice } from './Common';
import { number, percent } from '../format';

export interface MarketQuote {
  id: string;
  name: string;
  price: number | null;
  change_pct: number | null;
}
export interface MarketData {
  items: MarketQuote[];
}
export function Markets({
  selected,
  data,
  error,
  retry,
}: {
  selected: string[];
  data?: MarketData;
  error: string;
  retry: () => void;
}) {
  if (error) return <ErrorNotice message={error} retry={retry} />;
  if (!selected.length) return null;
  return (
    <section className="market-strip" aria-label="Piyasa özeti">
      {selected.map((id) => {
        const item = data?.items.find((item) => item.id === id);
        if (!item) return null;
        return (
          <article className="market-tile" key={id}>
            <span>{item.name}</span>
            <strong>{number(item.price)}</strong>
            <small>Değişim {percent(item.change_pct)}</small>
            {item.price == null && <small>Veri bekleniyor</small>}
          </article>
        );
      })}
    </section>
  );
}
