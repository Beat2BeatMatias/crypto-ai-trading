import { useEffect, useRef, useState } from "react";

export interface WSEvent { event: string; data: unknown; }

export function useWebSocket(url: string) {
  const [last, setLast] = useState<WSEvent | null>(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let cancelled = false;
    const connect = () => {
      const ws = new WebSocket(url);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!cancelled) setTimeout(connect, 3000);
      };
      ws.onmessage = (ev) => {
        try { setLast(JSON.parse(ev.data) as WSEvent); } catch { /* ignore */ }
      };
    };
    connect();
    return () => { cancelled = true; wsRef.current?.close(); };
  }, [url]);

  return { last, connected };
}
