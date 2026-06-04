# Playbook v0 — Bootstrap (sin datos históricos)

## Métricas del período
N/A — versión inicial.

## Setups que funcionaron
- C+B en TRENDING_UP: rebote en EMA50 1h con MACD 15m cruzando al alza.
- F+G: breakout de resistencia con volumen >1.5x y alineación 4h confirmada.
- A+E: RSI 1h saliendo de sobreventa con bid pressure en el order book.
- I+J en TRENDING_DOWN (futuros): rechazo en sobrecompra 15m/1h con MACD bajista confirmado.

## Patrones a evitar
- BUY sin G cuando el régimen es TRENDING_DOWN (EMA200 4h descendente).
- SHORT sin I/J cuando el régimen es TRENDING_UP (futuros).
- Spreads anómalos (>0.05%) — la confluencia E queda inválida.
- BUY cuando RSI 4h > 75 — cancela señales alcistas en timeframes menores.
- SHORT cuando RSI 4h < 25 sin confirmación de breakdown (F) — posible squeeze.
- Entradas en resistencia sin confirmación de volumen (F ausente).

## Contexto de mercado actual
Sin contexto histórico. El primer ciclo del Supervisor establecerá el baseline.

## Régimen esperado próximas 24h
NEUTRAL

## Reglas específicas
1. Requerir mínimo 2 confluencias del catálogo A–H antes de BUY.
2. En futuros, requerir mínimo 2 confluencias bajistas (I, J o F breakdown) antes de SHORT.
3. En RANGE, exigir H o C para BUY; para SHORT exigir I o rechazo en resistencia (F).
4. Si RSI 4h > 75 → solo HOLD o SELL, no abrir nuevas posiciones LONG.
5. Si RSI 4h < 25 → no abrir SHORT sin F (breakdown con volumen).
6. Si ATR del timeframe de referencia > 2x promedio 7d → el régimen es HIGH_VOLATILITY.
7. En las últimas 3 decisiones todas fueron HOLD con confluencias vacías → evaluar causa (d) del diagnóstico.

## Cambios vs playbook anterior
Primer playbook
