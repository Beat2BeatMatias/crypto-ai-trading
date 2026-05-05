# Playbook v0 — Bootstrap (sin datos historicos)

## Metricas del periodo
N/A — version inicial.

## Setups que funcionaron
- Pullback a EMA50 1h en tendencia alcista con RSI 5m saliendo de sobreventa.
- Breakout de Bollinger superior 15m con volumen >1.5x y MACD 1h alcista.

## Patrones a evitar
- Compras contra-tendencia macro (EMA200 4h descendente).
- Spreads anomalos (>0.05%).

## Contexto de mercado actual
Sin contexto historico. El primer ciclo del Supervisor establecera el baseline.

## Bias para proximas 24h
NEUTRAL

## Reglas especificas
1. Exigir >= 3 confluencias antes de BUY.
2. R:R minimo 1.5:1.
3. position_size_pct maximo 0.05 en bootstrap (primera semana).
4. Si daily P&L < -1.5% -> solo HOLD.
5. Si ATR(1h) > 1.5x promedio 7d -> reducir size 50%.
6. Si ultimas 5 decisiones fueron HOLD valido -> seguir HOLD, EXCEPTO si aparece un setup valido completo: pullback a EMA confirmado, breakout con volumen, o reversion con confirmacion de indicadores. Una unica señal aislada no invalida el HOLD.

## Cambios vs playbook anterior
N/A — primera version.
