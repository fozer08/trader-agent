import { useEffect, useRef, useState, type CSSProperties } from 'react';
import {
  ArrowUpRight,
  Bot,
  ChartNoAxesCombined,
  ChevronRight,
  LayoutDashboard,
  LockKeyhole,
  LogOut,
  Radio,
  PanelLeftClose,
  PanelLeftOpen,
  RefreshCw,
  SlidersHorizontal,
  Wallet,
  X,
} from 'lucide-react';
import { request, useResource } from './api';
import type {
  Metadata,
  Recommendation,
  PortfolioTab,
  ConversationPreferences,
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
import { Preferences } from './components/Preferences';
import { Markets, type MarketData } from './components/Markets';

interface Session {
  authenticated: boolean;
  login_required: boolean;
  user: { id: string; username: string } | null;
}
type Page = 'overview' | 'watchlist' | 'portfolio' | 'preferences';
const nav = [
  { id: 'overview' as const, label: 'Genel bakış', icon: LayoutDashboard },
  { id: 'watchlist' as const, label: 'Takip listesi', icon: ChartNoAxesCombined },
  { id: 'portfolio' as const, label: 'Portföyüm', icon: Wallet },
  { id: 'preferences' as const, label: 'Tercihler', icon: SlidersHorizontal },
];

function Login({ authenticated }: { authenticated: () => void }) {
  const [username, setUsername] = useState('');
  const [register, setRegister] = useState(false);
  const [password, setPassword] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  return (
    <div className="login-screen">
      <div className="login-art" aria-hidden="true">
        <span className="eyebrow">PİYASAYA KENDİ AÇINDAN BAK</span>
        <h2>
          Her hareketin
          <br />
          <em>bir hikâyesi var.</em>
        </h2>
        <svg viewBox="0 0 800 300" role="presentation">
          <defs>
            <linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1">
              <stop stopColor="#b6ef67" stopOpacity=".2" />
              <stop offset="1" stopColor="#b6ef67" stopOpacity="0" />
            </linearGradient>
          </defs>
          <path
            className="login-chart-fill"
            d="M0 245 L70 225 L115 240 L175 175 L225 190 L280 140 L330 160 L395 110 L455 155 L510 95 L550 110 L605 60 L660 80 L720 25 L800 40 L800 300 L0 300Z"
            fill="url(#chart-fill)"
          />
          <path
            d="M0 245 L70 225 L115 240 L175 175 L225 190 L280 140 L330 160 L395 110 L455 155 L510 95 L550 110 L605 60 L660 80 L720 25 L800 40"
            fill="none"
            stroke="#b6ef6730"
            strokeWidth="2"
          />
          <path
            className="login-chart-line"
            d="M0 245 L70 225 L115 240 L175 175 L225 190 L280 140 L330 160 L395 110 L455 155 L510 95 L550 110 L605 60 L660 80 L720 25 L800 40"
            fill="none"
            stroke="#b6ef67"
            strokeWidth="3"
          />
        </svg>
        <p>Takip et. Anlamlandır. Kendi planını oluştur.</p>
      </div>
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
          <h1>{register ? 'Hesabını oluştur' : 'Tekrar hoş geldin'}</h1>
          <p>Hisselerin, portföyün ve analiz asistanın seni bekliyor.</p>
        </div>
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (pending) return;
            setPending(true);
            setError('');
            try {
              await request(register ? '/register' : '/login', {
                method: 'POST',
                body: JSON.stringify({ username, password }),
              });
              authenticated();
            } catch (err) {
              setError(err instanceof Error ? err.message : 'Giriş yapılamadı.');
            } finally {
              setPending(false);
            }
          }}
        >
          <label>
            Kullanıcı adı
            <input
              aria-label="Kullanıcı adı"
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              pattern="[a-zA-Z0-9_]{3,32}"
              minLength={3}
              maxLength={32}
              required
              autoFocus
            />
          </label>
          <label>
            Parola
            <input
              type="password"
              autoComplete={register ? 'new-password' : 'current-password'}
              minLength={register ? 8 : undefined}
              maxLength={128}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {error && <ErrorNotice message={error} />}
          <button className="primary-button" disabled={pending}>
            {pending ? 'Bekleyin…' : register ? 'Hesap oluştur' : 'Giriş yap'}
            <ArrowUpRight size={16} />
          </button>
        </form>
        <button
          className="text-button"
          disabled={pending}
          onClick={() => {
            setRegister(!register);
            setError('');
          }}
        >
          {register ? 'Zaten hesabın var mı? Giriş yap' : 'Yeni misin? Hesap oluştur'}
        </button>
      </div>
    </div>
  );
}

interface DisplaySettings {
  portfolioTab?: PortfolioTab;
  sidebarCollapsed: boolean;
  chatWidth: number;
  markets: string[];
}
function AccountWorkspace({ session, logout }: { session: Session; logout: () => void }) {
  const profile = useResource<ConversationPreferences>('/preferences');
  const settings = useResource<DisplaySettings>('/settings');
  if (profile.error || settings.error)
    return (
      <ErrorNotice
        message={profile.error || settings.error}
        retry={() => {
          profile.refresh();
          settings.refresh();
        }}
      />
    );
  if (!profile.data || !settings.data) return <Loading rows={3} />;
  return (
    <Workspace
      session={session}
      logout={logout}
      initialPreferences={profile.data}
      initialSettings={settings.data}
    />
  );
}
function Workspace({
  session,
  logout,
  initialPreferences,
  initialSettings,
}: {
  session: Session;
  logout: () => void;
  initialPreferences: ConversationPreferences;
  initialSettings: DisplaySettings;
}) {
  const [page, setPage] = useState<Page>('overview');
  const [symbol, setSymbol] = useState<string>();
  const [chatOpen, setChatOpen] = useState(false);
  const [prompt, setPrompt] = useState<{ text: string; id: number } | null>(null);
  const [editor, setEditor] = useState<Editor>();
  const [notice, setNotice] = useState('');
  const [focusedPosition, setFocusedPosition] = useState<string>();
  const recommendations = useResource<{ items: Recommendation[] }>('/recommendations', 30000);
  const [portfolioVersion, setPortfolioVersion] = useState(0);
  const [preferences, applyPreferences] = useState(initialPreferences);
  const [savingPreferences, setSavingPreferences] = useState(false);
  const [settings, applySettings] = useState(() => ({
    ...initialSettings,
    sidebarCollapsed: window.innerWidth <= 700 || initialSettings.sidebarCollapsed,
  }));
  const [draftChatWidth, previewChatWidth] = useState<number | null>(null);
  const settingsQueue = useRef(Promise.resolve());
  function setSettings(next: DisplaySettings) {
    applySettings(next);
    settingsQueue.current = settingsQueue.current.then(async () => {
      try {
        await request('/settings', { method: 'PUT', body: JSON.stringify(next) });
      } catch (error) {
        setNotice(
          `Ekran tercihleri kaydedilemedi. ${error instanceof Error ? error.message : 'Tekrar deneyin.'}`,
        );
      }
    });
  }
  async function setPreferences(next: ConversationPreferences) {
    if (savingPreferences) return;
    setSavingPreferences(true);
    try {
      const saved = await request<ConversationPreferences>('/preferences', {
        method: 'PUT',
        body: JSON.stringify(next),
      });
      applyPreferences(saved);
      watchlist.refresh();
    } catch {
      setNotice('Trading tercihleri kaydedilemedi. Tekrar deneyin.');
    } finally {
      setSavingPreferences(false);
    }
  }
  const markets = useResource<MarketData>('/markets');
  const meta = useResource<Metadata>('/meta', 60000);
  const stocklist = useResource<Stocklist>(editor ? '/stocklist' : null);
  const watchlist = useResource<WatchlistData>(
    page === 'overview' || page === 'watchlist' ? '/watchlist' : null,
    20000,
  );
  const portfolio = useResource<PortfolioData>(
    page === 'overview' || page === 'portfolio' ? '/portfolio' : null,
    120000,
  );
  const navigate = (next: Page) => {
    setPage(next);
    if (window.innerWidth <= 700) setSettings({ ...settings, sidebarCollapsed: true });
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
    recommendations.refresh();
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
    recommendations.refresh();
  }
  return (
    <MarketContext.Provider value={meta.data}>
      <div
        className={`workspace ${chatOpen ? 'with-chat' : ''} ${settings.sidebarCollapsed ? 'sidebar-collapsed' : ''} ${(draftChatWidth ?? settings.chatWidth) > 600 ? 'chat-overlay' : ''}`}
        style={{ '--chat-width': `${draftChatWidth ?? settings.chatWidth}px` } as CSSProperties}
      >
        <aside className="sidebar" aria-label="Ana menü">
          <div className="sidebar-header">
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
            <button
              className="icon-button"
              aria-label={settings.sidebarCollapsed ? 'Menüyü genişlet' : 'Menüyü daralt'}
              aria-expanded={!settings.sidebarCollapsed}
              onClick={() =>
                setSettings({ ...settings, sidebarCollapsed: !settings.sidebarCollapsed })
              }
            >
              {settings.sidebarCollapsed ? (
                <PanelLeftOpen size={19} />
              ) : (
                <PanelLeftClose size={19} />
              )}
            </button>
          </div>
          <div className="workspace-label">
            <span className="status-dot" /> Kişisel çalışma alanı<span className="tag">BIST</span>
          </div>
          <span className="nav-label">ÇALIŞMA ALANI</span>
          <nav aria-label="Çalışma alanı">
            {nav.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                className={page === id ? 'active' : ''}
                onClick={() => navigate(id)}
                aria-label={label}
                title={label}
                aria-current={page === id ? 'page' : undefined}
              >
                <Icon size={18} />
                <span className="nav-text">{label}</span>
                {page === id && <ChevronRight className="nav-chevron" size={14} />}
              </button>
            ))}
          </nav>
          <div className="sidebar-bottom">
            <div className="feed-status">
              <Radio size={16} />
              <span>
                İş Yatırım<small>{meta.data?.delay_minutes ?? '—'} dk gecikmeli veri</small>
              </span>
            </div>
            {session.login_required && (
              <button
                className="text-button sidebar-logout"
                aria-label="Çıkış yap"
                title="Çıkış yap"
                onClick={logout}
              >
                <LogOut size={17} /> <span>Çıkış yap</span>
              </button>
            )}
            <small className="version">{session.user?.username}</small>
          </div>
        </aside>
        {!settings.sidebarCollapsed && (
          <button
            className="sidebar-backdrop"
            aria-label="Menüyü daraltmak için dışına tıkla"
            onClick={() => setSettings({ ...settings, sidebarCollapsed: true })}
          />
        )}
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
            </div>
          </header>
          <main>
            {page !== 'preferences' && (
              <>
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
                      <span>
                        {' '}
                        Sonraki seans: {date(meta.data.next_open, true, meta.data.timezone)}
                      </span>
                    )}
                  </div>
                )}
                <Markets
                  selected={settings.markets}
                  data={markets.data}
                  error={markets.error}
                  retry={markets.refresh}
                />
              </>
            )}
            {page === 'preferences' && (
              <Preferences
                value={preferences}
                change={(next) => void setPreferences(next)}
                saving={savingPreferences}
                markets={markets.data}
                marketsError={markets.error}
                retryMarkets={markets.refresh}
                selectedMarkets={settings.markets}
                changeMarkets={(selected) => setSettings({ ...settings, markets: selected })}
              />
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
                  preferences={preferences}
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
                ask={ask}
                tab={settings.portfolioTab ?? 'recommended'}
                setTab={(portfolioTab) => setSettings({ ...settings, portfolioTab })}
                recommendations={recommendations.data}
                recommendationsError={recommendations.error}
                refreshRecommendations={recommendations.refresh}
                focusId={focusedPosition}
                clearFocus={() => setFocusedPosition(undefined)}
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
          changed={changed}
          preferences={preferences}
          width={settings.chatWidth}
          previewWidth={previewChatWidth}
          resize={(chatWidth) => setSettings({ ...settings, chatWidth })}
          disabled={savingPreferences}
          portfolioRevision={portfolioVersion}
          acceptRecommendation={(recommendation) => setEditor({ mode: 'ACCEPT', recommendation })}
          openPosition={(id) => {
            setChatOpen(false);
            navigate('portfolio');
            setFocusedPosition(id);
          }}
        />
        {editor && (
          <TransactionEditor
            editor={editor}
            symbols={stocklist.data?.items ?? []}
            defaultHorizon={preferences.horizon}
            close={() => setEditor(undefined)}
            saved={(id) => {
              setEditor(undefined);
              setNotice('Portföy kaydı güncellendi.');
              setChatOpen(false);
              navigate('portfolio');
              setSettings({
                ...settings,
                portfolioTab: 'open',
                sidebarCollapsed: window.innerWidth <= 700 || settings.sidebarCollapsed,
              });
              setFocusedPosition(undefined);
              void id;
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
      <AccountWorkspace
        key={auth.data.user?.id}
        session={auth.data}
        logout={() => {
          void request('/logout', { method: 'POST' })
            .then(() => {
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
