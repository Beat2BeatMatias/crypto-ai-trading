import { useEffect, useRef, useState } from "react";
export function useWebSocket(url) {
    const [last, setLast] = useState(null);
    const [connected, setConnected] = useState(false);
    const wsRef = useRef(null);
    useEffect(() => {
        let cancelled = false;
        const connect = () => {
            const ws = new WebSocket(url);
            wsRef.current = ws;
            ws.onopen = () => setConnected(true);
            ws.onclose = () => {
                setConnected(false);
                if (!cancelled)
                    setTimeout(connect, 3000);
            };
            ws.onmessage = (ev) => {
                try {
                    setLast(JSON.parse(ev.data));
                }
                catch { /* ignore */ }
            };
        };
        connect();
        return () => { cancelled = true; wsRef.current?.close(); };
    }, [url]);
    return { last, connected };
}
