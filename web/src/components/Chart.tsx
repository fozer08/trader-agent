import { lazy, Suspense } from 'react';
import { Loading } from './Common';

const PriceChart = lazy(() => import('./PriceChart'));

export default function Chart(props: {
  symbol: string;
  timeframe?: 'D1' | 'M5' | 'M15';
  height?: number;
}) {
  return (
    <Suspense fallback={<Loading rows={5} />}>
      <PriceChart {...props} />
    </Suspense>
  );
}
