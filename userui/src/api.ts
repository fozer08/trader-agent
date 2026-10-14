import { useCallback, useEffect, useState } from 'react';
import type { ChatEvent, ConversationPreferences } from './types';

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    if (response.status === 401 && path !== '/login')
      window.dispatchEvent(new Event('auth-expired'));
    const detail =
      typeof data.detail === 'string'
        ? data.detail
        : response.status === 422
          ? 'Alanları kontrol edip tekrar deneyin.'
          : 'İstek tamamlanamadı.';
    throw new Error(detail);
  }
  return response.json();
}

export function useResource<T>(path: string | null, interval = 0) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(!!path);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    setData(undefined);
    setError('');
  }, [path]);
  useEffect(() => {
    if (!path) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      if (!active) return;
      setLoading(true);
      try {
        const result = await request<T>(path!, { signal: controller.signal });
        if (active) {
          setData(result);
          setError('');
        }
      } catch (err) {
        if (active && !(err instanceof DOMException && err.name === 'AbortError')) {
          setError(err instanceof Error ? err.message : 'Bağlantı kurulamadı.');
        }
      } finally {
        if (active) {
          setLoading(false);
          if (interval) timer = setTimeout(load, interval);
        }
      }
    }
    void load();
    return () => {
      active = false;
      controller.abort();
      clearTimeout(timer);
    };
  }, [path, interval, revision]);
  return { data, error, loading, refresh };
}

export async function streamChat(
  sessionId: string,
  message: string,
  onEvent: (event: ChatEvent) => void,
  signal: AbortSignal,
  preferences: ConversationPreferences,
) {
  const response = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId,
      message,
      output_format: 'markdown',
      preferences,
    }),
    signal,
  });
  if (!response.ok || !response.body) {
    if (response.status === 401) window.dispatchEvent(new Event('auth-expired'));
    throw new Error('Asistana bağlanılamadı. Tekrar deneyin.');
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  function consume(line: string) {
    if (!line.trim()) return;
    const event = JSON.parse(line) as ChatEvent;
    if (!['text', 'tool', 'error', 'recommendation'].includes(event.type))
      throw new Error('Geçersiz sohbet yanıtı.');
    if (
      event.type === 'recommendation' &&
      (typeof event.recommendation_id !== 'string' ||
        !/^[a-f0-9-]{36}$/.test(event.recommendation_id))
    )
      throw new Error('Geçersiz öneri yanıtı.');
    onEvent(event);
  }
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      lines.forEach(consume);
      if (done) {
        consume(buffer);
        break;
      }
    }
  } finally {
    reader.releaseLock();
  }
}
