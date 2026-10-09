import { useEffect, useRef, useState } from 'react';
import { ArrowUp, Bot, MessageSquare, RotateCcw, Square, X } from 'lucide-react';
import { request, streamChat } from '../api';
import { Markdown } from './Common';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  error?: string;
}
const toolNames: Record<string, string> = {
  scan: 'Takip listesi taranıyor',
  get_daily_indicators: 'Günlük göstergeler inceleniyor',
  get_intraday_indicators: 'Gün içi göstergeler inceleniyor',
  get_pulse: 'Fiyat verisi alınıyor',
  get_levels: 'Referans seviyeleri hesaplanıyor',
  list_portfolio: 'Portföy inceleniyor',
  get_market_news: 'Piyasa haberleri okunuyor',
  get_symbol_news: 'Hisse haberleri okunuyor',
  buy_position: 'Alım kaydı ekleniyor',
  sell_position: 'Satış kaydı ekleniyor',
  set_levels: 'Pozisyon planı güncelleniyor',
};
export default function Chat({
  open,
  close,
  prompt,
  instanceId,
  changed,
}: {
  open: boolean;
  close: () => void;
  prompt: { text: string; id: number } | null;
  instanceId?: string;
  changed: () => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [activity, setActivity] = useState('');
  const [notice, setNotice] = useState('');
  const controller = useRef<AbortController | null>(null);
  const session = useRef(
    sessionStorage.getItem('trader-chat-session') || `web:${crypto.randomUUID()}`,
  );
  const previousInstance = useRef(instanceId);
  const bottom = useRef<HTMLDivElement>(null);
  const textInput = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    sessionStorage.setItem('trader-chat-session', session.current);
    return () => controller.current?.abort();
  }, []);
  useEffect(() => {
    if (previousInstance.current && instanceId && previousInstance.current !== instanceId) {
      controller.current?.abort();
      session.current = `web:${crypto.randomUUID()}`;
      sessionStorage.setItem('trader-chat-session', session.current);
      setMessages([]);
      setNotice('Engine yeniden başladı. Yeni bir sohbet açıldı.');
    }
    if (instanceId) previousInstance.current = instanceId;
  }, [instanceId]);
  useEffect(() => {
    if (prompt) {
      setInput(prompt.text);
      textInput.current?.focus();
    }
  }, [prompt]);
  useEffect(() => {
    if (open) bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [messages, activity, open]);
  useEffect(() => {
    if (open) textInput.current?.focus();
  }, [open]);
  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    const id = crypto.randomUUID();
    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), role: 'user', text },
      { id, role: 'assistant', text: '' },
    ]);
    setInput('');
    setBusy(true);
    setActivity('Asistan düşünüyor');
    setNotice('');
    controller.current = new AbortController();
    try {
      await streamChat(
        session.current,
        text,
        (event) => {
          if (event.type === 'tool') setActivity(toolNames[event.name] ?? 'Veriler işleniyor');
          if (event.type === 'text') {
            setActivity('Yanıt hazırlanıyor');
            setMessages((current) =>
              current.map((message) =>
                message.id === id ? { ...message, text: message.text + event.content } : message,
              ),
            );
          }
          if (event.type === 'error') throw new Error(event.message);
        },
        controller.current.signal,
      );
    } catch (err) {
      const error =
        err instanceof DOMException && err.name === 'AbortError'
          ? 'Yanıt akışı durduruldu.'
          : err instanceof Error
            ? err.message
            : 'Sohbet tamamlanamadı.';
      setMessages((current) =>
        current.map((message) => (message.id === id ? { ...message, error } : message)),
      );
    } finally {
      setBusy(false);
      setActivity('');
      controller.current = null;
      changed();
    }
  }
  async function reset() {
    if (busy) return;
    try {
      await request('/reset', {
        method: 'POST',
        body: JSON.stringify({ session_id: session.current }),
      });
      session.current = `web:${crypto.randomUUID()}`;
      sessionStorage.setItem('trader-chat-session', session.current);
      setMessages([]);
      setNotice('Yeni sohbet açıldı.');
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Sohbet sıfırlanamadı.');
    }
  }
  return (
    <aside
      className={`chat-panel ${open ? 'is-open' : ''}`}
      aria-label="Analiz asistanı"
      aria-hidden={!open}
      inert={!open}
    >
      <div className="chat-heading">
        <div className="assistant-avatar">
          <Bot size={19} />
        </div>
        <div>
          <strong>Analiz asistanı</strong>
          <small>
            <span className="status-dot" /> Engine’e bağlı
          </small>
        </div>
        <div className="chat-header-actions">
          <button
            className="icon-button"
            aria-label="Yeni sohbet"
            disabled={busy}
            onClick={() => void reset()}
          >
            <RotateCcw size={15} />
          </button>
          <button className="icon-button" aria-label="Asistanı kapat" onClick={close}>
            <X size={17} />
          </button>
        </div>
      </div>
      <div className="chat-messages" aria-live="polite">
        {!messages.length && (
          <div className="chat-welcome">
            <div className="chat-symbol">
              <MessageSquare size={25} />
            </div>
            <span className="eyebrow">BİRLİKTE İNCELEYELİM</span>
            <h2>
              Veriden karara,
              <br />
              bir soru uzakta.
            </h2>
            <p>Bir hisseyi incele, pozisyon planını değerlendir veya takip listene birlikte bak.</p>
            {[
              'Takip listemde teknik görünüm nasıl?',
              'Portföyümde hangi risklere dikkat etmeliyim?',
              'THYAO için güncel teknik görünümü değerlendir.',
            ].map((text) => (
              <button
                className="suggested-prompt"
                key={text}
                onClick={() => {
                  setInput(text);
                  textInput.current?.focus();
                }}
              >
                {text}
                <ArrowUp size={14} />
              </button>
            ))}
          </div>
        )}
        {messages.map((message) => (
          <div key={message.id} className={`chat-message ${message.role}`}>
            <span className="message-label">{message.role === 'user' ? 'SEN' : 'ASİSTAN'}</span>
            {message.text && <Markdown>{message.text}</Markdown>}
            {message.error && <p className="negative">{message.error}</p>}
          </div>
        ))}
        {busy && (
          <div className="chat-activity" role="status">
            <span className="pulse-dot" />
            {activity}
          </div>
        )}
        {notice && <p className="chat-notice">{notice}</p>}
        <div ref={bottom} />
      </div>
      <form
        className="chat-composer"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <textarea
          ref={textInput}
          aria-label="Asistana mesaj"
          placeholder="Bir hisse veya pozisyon hakkında sor…"
          rows={3}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          maxLength={16000}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void send();
            }
          }}
        />
        <div className="composer-bottom">
          <small>{busy ? 'Yanıt bekleniyor' : 'Enter ile gönder · Shift + Enter yeni satır'}</small>
          {busy ? (
            <button
              type="button"
              className="send-button"
              aria-label="Yanıt akışını durdur"
              onClick={() => controller.current?.abort()}
            >
              <Square size={14} />
            </button>
          ) : (
            <button className="send-button" aria-label="Mesajı gönder" disabled={!input.trim()}>
              <ArrowUp size={18} />
            </button>
          )}
        </div>
      </form>
      <p className="chat-footnote">Sohbet hafızası engine yeniden başladığında sıfırlanır.</p>
    </aside>
  );
}
