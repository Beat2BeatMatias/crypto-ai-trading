# Playbook v0 — Bootstrap (sin datos historicos)

## Metricas del periodo
N/A — version inicial.

## Setups que funcionaron
- Pullback a EMA50 1h en tendencia alcista con RSI 5m saliendo de sobreventa.
- Breakout de Bollinger superior 15m con volumen >1.5x y MACD 1h alcista.
- RSI 1h entre 40-60 con MACD 15m cruzando al alza y precio sobre EMA20 1h.
- Imbalance de order book bid-heavy con RSI 5m subiendo desde zona 35-45.

## Patrones a evitar
- Compras contra-tendencia macro (EMA200 4h descendente).
- Spreads anomalos (>0.05%).
- BUY cuando RSI 4h > 75 (sobrecompra extrema en timeframe alto).
- Entradas en zona de resistencia sin volumen de confirmacion.

## Contexto de mercado actual
Sin contexto historico. El primer ciclo del Supervisor establecera el baseline.

## Bias para proximas 24h
NEUTRAL — activo en busqueda de setups de 2 confluencias.

## Confluencias validas para BUY (necesitas al menos 2)
- RSI 5m o 15m saliendo de zona 30-45 con pendiente positiva
- MACD 15m histograma positivo y creciente
- Precio sobre EMA20 1h
- Imbalance order book bid-heavy
- RSI 1h entre 40-65 (zona neutra-alcista sin sobrecompra)
- Precio rebotando en EMA50 1h o EMA20 4h

## Reglas de contexto
1. Si ultimas 3 decisiones fueron HOLD con confluencias=[] -> buscar setup activamente.
2. Si ATR del timeframe de referencia es anormalmente alto (>2x promedio) -> reducir size 50%.
3. Si RSI 4h > 75 -> evitar nuevas entradas, solo HOLD o SELL.

NOTA: Los umbrales de R:R, SL, stop diario y tamaño de posicion se definen en las
REGLAS ABSOLUTAS del sistema y aplican con los valores configurados. No repetirlos aqui.
