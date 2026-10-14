import { useEffect, useRef, useState } from 'react';
import { ArrowUp, Bot, MessageSquare, RotateCcw, Square, X } from 'lucide-react';
import { request, streamChat } from '../api';
import { Markdown } from './Common';
import { ChatRecommendation } from './Recommendations';
import type { ConversationPreferences, Recommendation } from '../types';
import { preferenceLabel } from '../preferences';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  error?: string;
  preferences?: ConversationPreferences;
  sentAt?: string;
  recommendationIds?: string[];
}
const toolNames: Record<string, string> = {
  publish_recommendation: 'Öneri hazırlanıyor',
  scan: 'Takip listesi taranıyor',
  analyze: 'Vadeye göre analiz hazırlanıyor',
  evaluate_trade: 'İşlem riski hesaplanıyor',
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
  changed,
  preferences,
  width,
  resize,
  previewWidth,
  disabled,
  portfolioRevision,
  acceptRecommendation,
  openPosition,
}: {
  open: boolean;
  close: () => void;
  prompt: { text: string; id: number } | null;
  changed: () => void;
  preferences: ConversationPreferences;
  width: number;
  resize: (width: number) => void;
  previewWidth: (width: number | null) => void;
  disabled: boolean;
  portfolioRevision: number;
  acceptRecommendation: (recommendation: Recommendation) => void;
  openPosition: (id: string) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [activity, setActivity] = useState('');
  const [notice, setNotice] = useState('');
  const controller = useRef<AbortController | null>(null);
  const session = useRef('');
  const [historyReady, setHistoryReady] = useState(false);
  const [conversations, setConversations] = useState<{ id: string; title: string }[]>([]);
  const [conversationId, setConversationId] = useState('');
  const [dragWidth, setDragWidth] = useState<number | null>(null);
  async function loadHistory() {
    setHistoryReady(false);
    try {
      const history = await request<{ conversationId: string; messages: Message[] }>('/history');
      const list = await request<{ items: { id: string; title: string }[] }>('/conversations');
      setMessages(history.messages);
      session.current = history.conversationId;
      setConversationId(history.conversationId);
      setConversations(list.items);
      setHistoryReady(true);
    } catch {
      setNotice('Sohbet geçmişi yüklenemedi.');
    }
  }
  const bottom = useRef<HTMLDivElement>(null);
  const textInput = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    void loadHistory();
    return () => controller.current?.abort();
  }, []);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 1250px)');
    const previous = document.body.style.overflow;
    const update = () => {
      document.body.style.overflow = open && (media.matches || width > 600) ? 'hidden' : previous;
    };
    update();
    media.addEventListener('change', update);
    return () => {
      media.removeEventListener('change', update);
      document.body.style.overflow = previous;
    };
  }, [open, width]);
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
    if (!text || busy || disabled || !historyReady) return;
    const id = crypto.randomUUID();
    const sentPreferences = { ...preferences };
    setMessages((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        role: 'user',
        text,
        preferences: sentPreferences,
        sentAt: new Date().toISOString(),
      },
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
          if (event.type === 'recommendation') {
            setMessages((current) =>
              current.map((message) =>
                message.id === id
                  ? {
                      ...message,
                      recommendationIds: [
                        ...new Set([...(message.recommendationIds ?? []), event.recommendation_id]),
                      ],
                    }
                  : message,
              ),
            );
          }
          if (event.type === 'error') throw new Error(event.message);
        },
        controller.current.signal,
        sentPreferences,
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
      try {
        const list = await request<{ items: { id: string; title: string }[] }>('/conversations');
        setConversations(list.items);
      } catch {
        setNotice('Sohbet listesi güncellenemedi.');
      }
    }
  }
  async function reset() {
    if (busy) return;
    try {
      await request('/reset', {
        method: 'POST',
        body: JSON.stringify({ session_id: session.current }),
      });
      await loadHistory();
      setNotice('Yeni sohbet açıldı.');
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Sohbet sıfırlanamadı.');
    }
  }
  return (
    <aside
      className={`chat-panel ${open ? 'is-open' : ''}`}
      style={{ width: dragWidth ?? width }}
      aria-label="Analiz asistanı"
      aria-hidden={!open}
      inert={!open}
    >
      <div
        role="separator"
        aria-label="Sohbet genişliği"
        aria-orientation="vertical"
        aria-valuemin={340}
        aria-valuemax={960}
        aria-valuenow={dragWidth ?? width}
        tabIndex={open ? 0 : -1}
        className="chat-resizer"
        onKeyDown={(event) => {
          if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
            event.preventDefault();
            resize(
              event.key === 'Home'
                ? 340
                : event.key === 'End'
                  ? 960
                  : Math.max(340, Math.min(960, width + (event.key === 'ArrowLeft' ? 40 : -40))),
            );
          }
        }}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          setDragWidth(width);
        }}
        onPointerMove={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            // Pointer coordinates can contain subpixels, while saved widths use whole pixels.
            const next = Math.max(
              340,
              Math.min(960, Math.round(window.innerWidth - event.clientX)),
            );
            setDragWidth(next);
            previewWidth(next);
          }
        }}
        onPointerUp={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId);
            resize(dragWidth ?? width);
            setDragWidth(null);
            previewWidth(null);
          }
        }}
        onPointerCancel={() => {
          setDragWidth(null);
          previewWidth(null);
        }}
      />
      <div className="chat-heading">
        <div className="assistant-avatar">
          <Bot size={19} />
        </div>
        <div>
          <strong>Analiz asistanı</strong>
          <small>
            <span className="status-dot" /> Piyasayı birlikte inceleyelim
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
      <div className="chat-history-picker">
        <select
          aria-label="Sohbet geçmişi"
          value={conversationId}
          disabled={busy}
          onChange={async (event) => {
            try {
              await request('/conversation', {
                method: 'POST',
                body: JSON.stringify({ id: event.target.value }),
              });
              await loadHistory();
            } catch {
              setNotice('Sohbet açılamadı.');
            }
          }}
        >
          {conversations.map((item) => (
            <option key={item.id} value={item.id}>
              {item.title}
            </option>
          ))}
        </select>
        {!historyReady && <button onClick={() => void loadHistory()}>Geçmişi yükle</button>}
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
            {message.preferences && message.sentAt && (
              <small className="message-preferences">
                <time dateTime={message.sentAt}>
                  {new Date(message.sentAt).toLocaleString('tr-TR', {
                    day: '2-digit',
                    month: 'short',
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </time>
                {' · '}
                {preferenceLabel(message.preferences)}
              </small>
            )}
            {message.text && <Markdown>{message.text}</Markdown>}
            {message.recommendationIds?.map((id) => (
              <ChatRecommendation
                key={`${id}-${portfolioRevision}`}
                id={id}
                accept={acceptRecommendation}
                openPosition={openPosition}
              />
            ))}
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
            <button
              className="send-button"
              aria-label="Mesajı gönder"
              disabled={!input.trim() || disabled || !historyReady}
            >
              <ArrowUp size={18} />
            </button>
          )}
        </div>
      </form>
    </aside>
  );
}
