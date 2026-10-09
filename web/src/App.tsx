import { useEffect, useState } from 'react';
import {
  ArrowUpRight,
  Bot,
  ChartNoAxesCombined,
  ChevronRight,
  LayoutDashboard,
  LockKeyhole,
  LogOut,
  Radio,
  RefreshCw,
  Wallet,
  X,
} from 'lucide-react';
import { request, useResource } from './api';
import type {
  Metadata,
  Portfolio as PortfolioData,
  Stocklist,
  Watchlist as WatchlistData,
} from './types';
import { date } from './format';
import { MarketContext, sessionLabel } from './market';
import Dashboard from './components/Dashboard';
import Watchlist from './components/Watchlist';
import SymbolDetail from './components/SymbolDetail';
import Portfolio, { TransactionEditor, type Editor } from './components/Portfolio';
import Chat from './components/Chat';
import { ErrorNotice, Loading } from './components/Common';

interface Session {
  authenticated: boolean;
  login_required: boolean;
}
type Page = 'overview' | 'watchlist' | 'portfolio';
const nav = [
  { id: 'overview' as const, label: 'Genel bakış', icon: LayoutDashboard },
  { id: 'watchlist' as const, label: 'Takip listesi', icon: ChartNoAxesCombined },
  { id: 'portfolio' as const, label: 'Portföyüm', icon: Wallet },
];

function Login({ authenticated }: { authenticated: () => void }) {
  const [password, setPassword] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  return (
    <div className="login-screen">
      <div className="login-card">
        <div className="brand">
          <span className="brand-icon">
            <ChartNoAxesCombined size={23} />
          </span>
          <strong>
            trader<span>agent</span>
          </strong>
        </div>
        <div className="login-heading">
          <LockKeyhole size={24} />
          <h1>Çalışma alanına giriş</h1>
          <p>Hisselerin, portföyün ve analiz asistanın seni bekliyor.</p>
        </div>
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (pending) return;
            setPending(true);
            setError('');
            try {
              await request('/login', { method: 'POST', body: JSON.stringify({ password }) });
              authenticated();
            } catch (err) {
              setError(err instanceof Error ? err.message : 'Giriş yapılamadı.');
            } finally {
              setPending(false);
            }
          }}
        >
          <label>
            Parola
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
              autoFocus
            />
          </label>
          {error && <ErrorNotice message={error} />}
          <button className="primary-button" disabled={pending}>
            {pending ? 'Giriş yapılıyor…' : 'Giriş yap'}
            <ArrowUpRight size={16} />
          </button>
        </form>
        <small>Kişisel BIST çalışma alanın</small>
      </div>
    </div>
  );
}

function Workspace({ session, logout }: { session: Session; logout: () => void }) {
  const [page, setPage] = useState<Page>('overview');
  const [symbol, setSymbol] = useState<string>();
  const [chatOpen, setChatOpen] = useState(false);
  const [prompt, setPrompt] = useState<{ text: string; id: number } | null>(null);
  const [editor, setEditor] = useState<Editor>();
  const [notice, setNotice] = useState('');
  const [portfolioVersion, setPortfolioVersion] = useState(0);
  const meta = useResource<Metadata>('/meta', 60000);
  const stocklist = useResource<Stocklist>(editor ? '/stocklist' : null);
  const watchlist = useResource<WatchlistData>(page !== 'portfolio' ? '/watchlist' : null, 20000);
  const portfolio = useResource<PortfolioData>(
    page === 'overview' || page === 'portfolio' ? '/portfolio' : null,
    120000,
  );
  const navigate = (next: Page) => {
    setPage(next);
    setSymbol(undefined);
    window.scrollTo({ top: 0 });
  };
  const select = (next: string) => {
    setSymbol(next);
    setPage('watchlist');
    window.scrollTo({ top: 0 });
  };
  const ask = (text: string) => {
    setChatOpen(true);
    setPrompt({ text, id: Date.now() });
  };
  const changed = () => {
    portfolio.refresh();
    setPortfolioVersion((value) => value + 1);
  };
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(''), 5000);
    return () => clearTimeout(timer);
  }, [notice]);
  function refresh() {
    meta.refresh();
    watchlist.refresh();
    portfolio.refresh();
  }
  return (
    <MarketContext.Provider value={meta.data}>
      <div className={`workspace ${chatOpen ? 'with-chat' : ''}`}>
        <aside className="sidebar">
          <button
            className="brand"
            onClick={() => navigate('overview')}
            aria-label="Trader Agent ana sayfa"
          >
            <span className="brand-icon">
              <ChartNoAxesCombined size={22} />
            </span>
            <strong>
              trader<span>agent</span>
            </strong>
          </button>
          <div className="workspace-label">
            <span className="status-dot" /> Kişisel çalışma alanı<span className="tag">BIST</span>
          </div>
          <span className="nav-label">ÇALIŞMA ALANI</span>
          <nav>
            {nav.map(({ id, label, icon: Icon }) => (
              <button key={id} className={page === id ? 'active' : ''} onClick={() => navigate(id)}>
                <Icon size={18} />
                <span>{label}</span>
                {page === id && <ChevronRight size={14} />}
              </button>
            ))}
          </nav>
          <div className="sidebar-assistant">
            <div className="assistant-orbit">
              <Bot size={22} />
            </div>
            <strong>Birlikte daha net bak.</strong>
            <p>Göstergeleri yorumlamak için analiz asistanına sor.</p>
            <button onClick={() => setChatOpen(true)}>
              Asistanı aç <ArrowUpRight size={15} />
            </button>
          </div>
          <div className="sidebar-bottom">
            <div className="feed-status">
              <Radio size={16} />
              <span>
                İş Yatırım<small>{meta.data?.delay_minutes ?? '—'} dk gecikmeli veri</small>
              </span>
            </div>
            {session.login_required && (
              <button className="text-button" onClick={logout}>
                <LogOut size={15} /> Çıkış yap
              </button>
            )}
            <small className="version">TRADER AGENT / 01</small>
          </div>
        </aside>
        <div className="main-area">
          <header className="topbar">
            <div className="breadcrumbs">
              <span>Çalışma alanı</span>
              <ChevronRight size={13} />
              <strong>{symbol ?? nav.find((item) => item.id === page)?.label}</strong>
            </div>
            <div className="topbar-actions">
              <span className="session-badge">
                <span className={`status-dot ${meta.data?.scheduled_open ? '' : 'closed'}`} />
                {sessionLabel(meta.data)}
              </span>
              <button
                className={`icon-button ${meta.loading || watchlist.loading ? 'is-refreshing' : ''}`}
                aria-label="Verileri yenile"
                onClick={refresh}
              >
                <RefreshCw size={16} />
              </button>
              <button
                className={`assistant-toggle ${chatOpen ? 'active' : ''}`}
                onClick={() => setChatOpen(!chatOpen)}
              >
                <Bot size={17} />
                <span>Asistan</span>
              </button>
              {session.login_required && (
                <button
                  className="icon-button mobile-logout"
                  aria-label="Çıkış yap"
                  onClick={logout}
                >
                  <LogOut size={16} />
                </button>
              )}
            </div>
          </header>
          <main>
            <div className="workspace-meta">
              <span>
                {date(meta.data?.server_time, false, meta.data?.timezone)}{' '}
                <span className="muted">/ {meta.data?.timezone ?? '—'}</span>
              </span>
              <span className="delay-label">
                <span />
                Veri {meta.data?.delay_minutes ?? '—'} dk gecikmeli
              </span>
            </div>
            {meta.error && <ErrorNotice message={meta.error} retry={meta.refresh} />}
            {meta.data && meta.data.day_kind !== 'normal' && (
              <div className="inline-note market-calendar-note" role="status">
                <span>
                  {sessionLabel(meta.data)}
                  {meta.data.day_kind === 'half_day' && meta.data.reason
                    ? ` · ${meta.data.reason}`
                    : ''}
                </span>
                {meta.data.next_open && (
                  <span> Sonraki seans: {date(meta.data.next_open, true, meta.data.timezone)}</span>
                )}
              </div>
            )}
            {page === 'overview' && (
              <>
                {portfolio.error && (
                  <ErrorNotice message={portfolio.error} retry={portfolio.refresh} />
                )}
                {watchlist.error && (
                  <ErrorNotice message={watchlist.error} retry={watchlist.refresh} />
                )}
                <Dashboard
                  watchlist={watchlist.data}
                  portfolio={portfolio.data}
                  select={select}
                  ask={ask}
                  showPortfolio={() => navigate('portfolio')}
                />
              </>
            )}
            {page === 'watchlist' &&
              (symbol ? (
                <SymbolDetail
                  key={symbol}
                  symbol={symbol}
                  back={() => setSymbol(undefined)}
                  ask={ask}
                  buy={(value) => setEditor({ mode: 'BUY', symbol: value })}
                />
              ) : (
                <>
                  <div className="page-heading">
                    <div>
                      <span className="eyebrow">PİYASAYI TAKİP ET</span>
                      <h1>Takip listesi</h1>
                      <p>
                        {watchlist.data?.items.length ?? meta.data?.watchlist_count ?? '—'} hisse.
                        Tek bir bakışta fiyat hareketi ve teknik görünüm.
                      </p>
                    </div>
                    <button
                      className="secondary-button"
                      onClick={() =>
                        ask(
                          'Takip listemi teknik göstergelerle karşılaştır. Öne çıkan hisseleri ve belirsizlikleri açıkla.',
                        )
                      }
                    >
                      <Bot size={16} /> Asistanla değerlendir
                    </button>
                  </div>
                  <Watchlist
                    data={watchlist.data}
                    error={watchlist.error}
                    refresh={watchlist.refresh}
                    select={select}
                    changed={() => {
                      watchlist.refresh();
                      meta.refresh();
                    }}
                  />
                </>
              ))}
            {page === 'portfolio' && (
              <Portfolio
                key={portfolioVersion}
                data={portfolio.data}
                error={portfolio.error}
                refresh={portfolio.refresh}
                edit={setEditor}
                select={select}
              />
            )}
            <footer className="page-footer">
              <span>Trader Agent</span>
              <span>BIST · Teknik analiz & portföy takibi</span>
            </footer>
          </main>
        </div>
        {chatOpen && (
          <button
            className="chat-backdrop"
            aria-label="Asistan panelini kapat"
            onClick={() => setChatOpen(false)}
          />
        )}
        <Chat
          open={chatOpen}
          close={() => setChatOpen(false)}
          prompt={prompt}
          instanceId={meta.data?.instance_id}
          changed={changed}
        />
        {editor && (
          <TransactionEditor
            editor={editor}
            symbols={stocklist.data?.items ?? []}
            close={() => setEditor(undefined)}
            saved={() => {
              setEditor(undefined);
              setNotice('Portföy kaydı güncellendi.');
              changed();
            }}
          />
        )}
        {notice && (
          <div className="toast" role="status">
            {notice}
            <button
              className="icon-button"
              aria-label="Bildirimi kapat"
              onClick={() => setNotice('')}
            >
              <X size={15} />
            </button>
          </div>
        )}
      </div>
    </MarketContext.Provider>
  );
}

export default function App() {
  const auth = useResource<Session>('/session');
  const [logoutError, setLogoutError] = useState('');
  useEffect(() => {
    const expired = () => auth.refresh();
    window.addEventListener('auth-expired', expired);
    return () => window.removeEventListener('auth-expired', expired);
  }, [auth.refresh]);
  if (!auth.data)
    return (
      <div className="startup">
        {auth.error ? (
          <ErrorNotice message={auth.error} retry={auth.refresh} />
        ) : (
          <Loading rows={3} />
        )}
      </div>
    );
  if (!auth.data.authenticated) return <Login authenticated={auth.refresh} />;
  return (
    <>
      {logoutError && (
        <div className="toast" role="alert">
          {logoutError}
          <button
            className="icon-button"
            aria-label="Hata bildirimini kapat"
            onClick={() => setLogoutError('')}
          >
            <X size={15} />
          </button>
        </div>
      )}
      <Workspace
        session={auth.data}
        logout={() => {
          void request('/logout', { method: 'POST' })
            .then(() => {
              sessionStorage.removeItem('trader-chat-session');
              auth.refresh();
            })
            .catch(() =>
              setLogoutError('Çıkış yapılamadı. Bağlantıyı kontrol edip tekrar deneyin.'),
            );
        }}
      />
    </>
  );
}
