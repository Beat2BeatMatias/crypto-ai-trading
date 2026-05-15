# Playbook v0 — Bootstrap (sin datos históricos)

## Métricas del período
N/A — versión inicial.

## Setups que funcionaron
- C+B en TRENDING_UP: rebote en EMA50 1h con MACD 15m cruzando al alza.
- F+G: breakout de resistencia con volumen >1.5x y alineación 4h confirmada.
- A+E: RSI 1h saliendo de sobreventa con bid pressure en el order book.

## Patrones a evitar
- BUY sin G cuando el régimen es TRENDING_DOWN (EMA200 4h descendente).
- Spreads anómalos (>0.05%) — la confluencia E queda inválida.
- BUY cuando RSI 4h > 75 — cancela señales alcistas en timeframes menores.
- Entradas en resistencia sin confirmación de volumen (F ausente).

## Contexto de mercado actual
Sin contexto histórico. El primer ciclo del Supervisor establecerá el baseline.

## Régimen esperado próximas 24h
NEUTRAL

## Reglas específicas
1. Requerir mínimo 2 confluencias del catálogo A-H antes de BUY.
2. En RANGE, exigir H o C como confluencia obligatoria.
3. Si RSI 4h > 75 → solo HOLD o SELL, no abrir nuevas posiciones.
4. Si ATR del timeframe de referencia > 2x promedio 7d → el régimen es HIGH_VOLATILITY.
5. En las últimas 3 decisiones todas fueron HOLD con confluencias vacías → evaluar causa (d) del diagnóstico.

## Cambios vs playbook anterior
Primer playbook
