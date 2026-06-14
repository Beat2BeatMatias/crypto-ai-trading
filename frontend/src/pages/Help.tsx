function SubTitle({ children }: { children: React.ReactNode }) {
  return <h3 className="text-md font-semibold text-emerald-400 mt-6 mb-2">{children}</h3>;
}

function Badge({ children, color }: { children: React.ReactNode; color: string }) {
  const cls = color === "emerald" ? "bg-emerald-900/50 text-emerald-300" :
    color === "amber" ? "bg-amber-900/50 text-amber-300" :
    color === "sky" ? "bg-sky-900/50 text-sky-300" :
    color === "red" ? "bg-red-900/50 text-red-300" :
    color === "purple" ? "bg-purple-900/50 text-purple-300" :
    color === "zinc" ? "bg-zinc-800 text-zinc-300" :
    "bg-zinc-800 text-zinc-300";
  return <span className={`inline-block px-2 py-0.5 rounded text-xs font-mono ${cls}`}>{children}</span>;
}

function Code({ children }: { children: React.ReactNode }) {
  return <code className="text-emerald-300 bg-zinc-800 px-1 rounded text-xs">{children}</code>;
}

function Table({ headers, rows }: { headers: string[]; rows: string[][] }) {
  return (
    <div className="overflow-x-auto rounded border border-zinc-700">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-zinc-800">
            {headers.map((h, i) => (
              <th key={i} className="px-3 py-2 text-left font-semibold text-zinc-300 border-b border-zinc-700">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className={i % 2 === 0 ? "bg-zinc-900" : "bg-zinc-900/50"}>
              {row.map((cell, j) => (
                <td key={j} className="px-3 py-2 text-zinc-300 border-t border-zinc-800">{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl bg-zinc-900 border border-zinc-800 p-5 mb-4">
      <div className="text-sm font-semibold text-zinc-400 uppercase tracking-wide mb-3">{title}</div>
      {children}
    </div>
  );
}

export function Help() {
  return (
    <div className="max-w-4xl mx-auto space-y-8">

      {/* ───── INTRO ───── */}
      <div>
        <h1 className="text-2xl font-bold text-zinc-100 mb-2">Ayuda</h1>
        <p className="text-zinc-400 text-sm">
          Documentación de arquitectura, proceso de toma de decisiones, fórmula de confianza,
          restricciones y componentes del sistema Crypto AI Trading.
        </p>
      </div>

      {/* ───── 1. ARQUITECTURA ───── */}
      <Card title="1. Arquitectura en Alto Nivel">
        <p className="text-zinc-400 text-sm mb-4">
          El sistema se compone de tres servicios que se comunican exclusivamente vía Postgres
          (sin IPC directo). Postgres es la fuente única de verdad (<em>single source of truth</em>).
        </p>

        {/* Diagrama de arquitectura */}
        <div className="font-mono text-xs text-zinc-300 leading-relaxed whitespace-pre bg-zinc-950 rounded-lg p-4 border border-zinc-800 mb-4 overflow-x-auto">
{`┌─────────────────────────────────────────────────────────────┐
│                       Postgres 17                              │
│   (decisions, trades, positions, config, playbook, outcomes)   │
└──────────▲───────────────────────────────▲─────────────────────┘
           │                               │
   RW (todo)│                        R (todo) + W (config)
           │                               │
┌──────────┴──────────────┐   ┌──────────┴──────────────────────┐
│     trading-engine       │   │            web                   │
│  (Python 3.12, asyncio)  │   │   (FastAPI + uvicorn :8100)      │
│  Sin HTTP server         │   │   REST + WebSocket               │
│  Bucle autónomo continuo │   │   Sirve datos al frontend        │
└──────────▲───────────────┘   └──────────▲───────────────────────┘
           │                               │
     CCXT (REST/WS)                   HTTP / WS
           │                               │
     ┌────┴──────┐                  ┌──────┴────────┐
     │  Binance   │                  │   Frontend     │
     │ testnet /  │                  │  React + Vite  │
     │ mainnet    │                  │  Tailwind v4   │
     └───────────┘                  │  Puerto 3100   │
                                    └───────────────┘`}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm text-zinc-400">
          <div className="rounded-lg bg-zinc-900/50 p-3 border border-zinc-800">
            <div className="font-semibold text-zinc-200 mb-1">trading-engine</div>
            <div className="text-xs">Bucle autónomo sin HTTP. Escribe decisiones, trades, posiciones, outcomes, config. Corre cada <Code>decisor_interval_min</Code> (default 5 min).</div>
          </div>
          <div className="rounded-lg bg-zinc-900/50 p-3 border border-zinc-800">
            <div className="font-semibold text-zinc-200 mb-1">web</div>
            <div className="text-xs">FastAPI en puerto 8100. Lee todo (solo escribe config y playbook). Sirve REST + WebSocket al frontend.</div>
          </div>
          <div className="rounded-lg bg-zinc-900/50 p-3 border border-zinc-800">
            <div className="font-semibold text-zinc-200 mb-1">frontend</div>
            <div className="text-xs">React + Vite + Tailwind en puerto 3100. UI en español (es-AR). Sin i18n library — texto hardcodeado en español.</div>
          </div>
        </div>
      </Card>

      {/* ───── 2. PROCESO DE DECISIÓN ───── */}
      <Card title="2. Proceso de Toma de Decisiones">
        <p className="text-zinc-400 text-sm mb-4">
          El <Badge color="emerald">Decisor</Badge> ejecuta un ciclo completo cada <Code>decisor_interval_min</Code> minutos.
          El <Badge color="purple">Supervisor</Badge> corre diariamente (00:00 UTC) o manualmente.
        </p>

        <SubTitle>Ciclo del Decisor</SubTitle>
        <div className="bg-zinc-950 rounded-lg p-4 border border-zinc-800 mb-4 text-sm text-zinc-300 space-y-2 font-mono text-xs">
          <div>1. <span className="text-zinc-500">Verificar</span> engine_paused → si paused, return</div>
          <div>2. <span className="text-zinc-500">Leer</span> ~30 config keys de ConfigStore</div>
          <div>3. <span className="text-zinc-500">Colectar</span> OHLCV (5 timeframes: 1m, 5m, 15m, 1h, 4h)</div>
          <div>4. <span className="text-zinc-500">Calcular</span> indicadores técnicos (RSI, MACD, EMA, BB, ATR, ADX, VWAP)</div>
          <div>5. <span className="text-zinc-500">Obtener</span> balance, orderbook, fees</div>
          <div>6. <span className="text-zinc-500">ContextBuilder.build()</span> → Bloques A–K</div>
          <div>7. <span className="text-zinc-500">Decisor.decide()</span></div>
          <div className="pl-4">a. Early-exit si indicadores críticos son null → HOLD conf=0.95</div>
          <div className="pl-4">b. Render system prompt + user prompt (contexto)</div>
          <div className="pl-4">c. Auto-consistencia: si N&gt;1, llama LLM N veces y vota mayoría</div>
          <div className="pl-4">d. LLM call → parse JSON → Pydantic validation (DecisorOutput)</div>
          <div className="pl-4">e. Filtrar códigos de confluencia inválidos</div>
          <div className="pl-4">f. <Code>apply_server_confidence</Code> → recalcula confidence_base</div>
          <div className="pl-4">g. <Code>apply_risk_based_sizing</Code> → recalcula position_size</div>
          <div className="pl-4">h. CoherenceChecker.evaluate() → reglas C1–C10</div>
          <div className="pl-4">i. Two-pass (si activo y hay warnings críticos) → segunda llamada LLM</div>
          <div className="pl-4">j. strict_mode → warnings críticos fuerzan HOLD</div>
          <div className="pl-4">k. Persistir decisión en DB</div>
          <div>8. <span className="text-zinc-500">RiskGate.validate()</span> → reglas R0–R15</div>
          <div>9. Si rechazado → update rejected_reason, return</div>
          <div>10. <span className="text-zinc-500">Ejecutar</span>: BUY→open LONG, SHORT→open SHORT, SELL→close, HOLD→nada</div>
        </div>

        <SubTitle>Ciclo del Supervisor</SubTitle>
        <div className="bg-zinc-950 rounded-lg p-4 border border-zinc-800 text-sm text-zinc-300 space-y-2 font-mono text-xs">
          <div>1. <span className="text-zinc-500">Métricas</span> 24h: trades, win rate, P&amp;L, regímenes, coherencia</div>
          <div>2. Si closed_trades &lt; mínimo → modo diagnóstico</div>
          <div>3. <span className="text-zinc-500">Fase 1 — Ratificación</span> del playbook activo</div>
          <div className="pl-4">a. Guardrails determinísticos (edad, WR delta, kill switch, etc.)</div>
          <div className="pl-4">b. Si ningún guardrail se activa → LLM eval: ratificar o regenerar</div>
          <div>4. <span className="text-zinc-500">Fase 2 — Regeneración</span> (solo si es necesario)</div>
          <div className="pl-4">a. LLM produce nuevo playbook en Markdown</div>
          <div className="pl-4">b. Guarda PlaybookVersion (versiones anteriores → active=false)</div>
          <div>5. <span className="text-zinc-500">Fase 3 — Config suggestions</span></div>
          <div className="pl-4">a. Genera sugerencias de parámetros estructurados</div>
          <div className="pl-4">b. Auto-apply solo dentro de _SAFE_BOUNDS</div>
          <div>6. <span className="text-zinc-500">Promoción</span> de confluencias candidatas</div>
          <div>7. <span className="text-zinc-500">Persistir</span> Decision (agent="supervisor")</div>
        </div>
      </Card>

      {/* ───── 3. CONFLUENCIAS ───── */}
      <Card title="3. Confluencias">
        <p className="text-zinc-400 text-sm mb-4">
          Las confluencias son <strong>señales técnicas codificadas</strong> que el LLM declara en su output JSON.
          Son los bloques fundamentales de la fórmula de confianza. Se clasifican en tres categorías:
        </p>

        <SubTitle>Catálogo Fijo Bullish (A–H)</SubTitle>
        <p className="text-zinc-500 text-xs mb-2">Siempre presentes en el system prompt del Decisor.</p>
        <Table headers={["Código", "Nombre", "Descripción"]} rows={[
          ["A", "RSI_OVERSOLD_BOUNCE", "RSI 15m/1h saliendo de &lt;30 con vela alcista"],
          ["B", "MACD_BULLISH_CROSS", "MACD &gt; Signal en 15m/1h con histograma creciente"],
          ["C", "EMA_SUPPORT_HOLD", "Rebote en EMA20/50/200 (1h/4h) con mecha"],
          ["D", "BB_LOWER_REVERSAL", "BB% 5m &lt;5 con vela de reversión"],
          ["E", "ORDERBOOK_BID_PRESSURE", "Desequilibrio &gt;0.6 + bid wall &lt;0.3% del precio"],
          ["F", "BREAKOUT_VOL_CONFIRMED", "Breakout con volumen &gt;1.5× media 20 períodos"],
          ["G", "HIGHER_TF_ALIGNMENT", "RSI 4h&gt;50 + EMA20_4h &gt; EMA50_4h + precio &gt; EMA20_1h"],
          ["H", "RANGE_SUPPORT_TOUCH", "Precio en banda inferior de rango definido"],
        ]} />

        <SubTitle>Catálogo Fijo Bearish (I–J) — Solo Futuros</SubTitle>
        <p className="text-zinc-500 text-xs mb-2">Siempre en system prompt, solo disponibles cuando <Code>trading_product=futures</Code>.</p>
        <Table headers={["Código", "Nombre", "Descripción"]} rows={[
          ["I", "RSI_OVERBOUGHT_REJECTION", "RSI 15m/1h &gt;65 con rechazo bajista"],
          ["J", "MACD_BEARISH_CROSS", "MACD &lt; Signal en 15m/1h con histograma decreciente"],
        ]} />

        <SubTitle>Confluencias Promovidas (K–Z) — Patrones Aprendidos</SubTitle>
        <p className="text-zinc-400 text-sm mb-2">
          No están hardcodeadas. Surgen de un pipeline de aprendizaje:
        </p>
        <div className="bg-zinc-950 rounded-lg p-4 border border-zinc-800 text-sm text-zinc-300 font-mono text-xs space-y-1 mb-3">
          <div>1. <span className="text-zinc-500">Post-mortem</span> analiza malas decisiones → genera candidato</div>
          <div>2. Candidato almacenado en <Code>confluence_candidates</Code></div>
          <div>3. <span className="text-zinc-500">Supervisor</span> (u operador vía UI) promueve a <Code>confluence_registry</Code></div>
          <div>4. Criterios: ≥3 ocurrencias en 7 días, verify_spec testeable, max 5 activas</div>
          <div>5. Cada confluencia promovida tiene <Code>definition_md</Code> + tag direccional [LONG/SHORT/AMBOS]</div>
        </div>
        <p className="text-zinc-500 text-xs">
          El CoherenceChecker valida que K–Z cumplan su <Code>verify_spec</Code> (C8) y que el tag direccional
          coincida con la acción (C9).
        </p>
      </Card>

      {/* ───── 4. PLAYBOOK ───── */}
      <Card title="4. Playbook">
        <p className="text-zinc-400 text-sm mb-3">
          El playbook es un documento en <strong>Markdown</strong> producido por el Supervisor que sirve como
          guía táctica para el Decisor. Se inyecta en el <strong>Bloque J</strong> del contexto del LLM.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm text-zinc-400 mb-3">
          <div className="rounded-lg bg-zinc-900/50 p-3 border border-zinc-800">
            <div className="font-semibold text-zinc-200 mb-1">Contenido</div>
            <ul className="list-disc list-inside text-xs space-y-1">
              <li>Contexto del régimen de mercado</li>
              <li>Setups a buscar</li>
              <li>Patrones a evitar</li>
              <li>Reglas específicas para condiciones actuales</li>
              <li>Win rate baseline esperado</li>
            </ul>
          </div>
          <div className="rounded-lg bg-zinc-900/50 p-3 border border-zinc-800">
            <div className="font-semibold text-zinc-200 mb-1">Jerarquía (posición 3)</div>
            <ol className="list-decimal list-inside text-xs space-y-1">
              <li>Risk Rules (más prioritario)</li>
              <li>Risk Parameters</li>
              <li><strong>Playbook</strong></li>
              <li>Technical Confluences</li>
            </ol>
          </div>
        </div>
        <p className="text-zinc-500 text-xs">
          El Supervisor regenera el playbook diariamente si es necesario. Edad máxima: <Code>max_playbook_age_days</Code> (default 7).
          El operador puede hacer rollback a cualquier versión anterior desde la UI.
        </p>
      </Card>

      {/* ───── 5. FÓRMULA DE CONFIANZA ───── */}
      <Card title="5. Fórmula de Confianza">
        <p className="text-zinc-400 text-sm mb-3">
          El cálculo de confianza es <strong>server-side</strong> en <Code>shared/confidence.py</Code>.
          El LLM solo declara <Code>confidence_adjustment</Code> (acotado a ±<Code>subjective_adj_max</Code>, default 0.10).
          La fórmula final:
        </p>
        <div className="bg-zinc-950 rounded-lg p-4 border border-zinc-700 mb-4 text-center">
          <code className="text-emerald-300 text-sm font-mono">
            confidence = clip(conf_base_table(count) × quality_factor × regime_factor + confidence_adjustment, 0, 1)
          </code>
        </div>

        <h4 className="text-sm font-semibold text-zinc-200 mb-2">Confidence Base por Tabla</h4>
        <Table headers={["Confluencias", "conf_base", "Descripción"]} rows={[
          ["0", "0.35", "Sin confluencias activas"],
          ["1", "0.55", "1 confluencia activa"],
          ["2", "0.75", "2 confluencias activas"],
          ["3", "0.88", "3 confluencias activas"],
          ["4+", "1.00", "4 o más confluencias (cap)"],
        ]} />

        <h4 className="text-sm font-semibold text-zinc-200 mt-4 mb-2">Componentes de la Fórmula</h4>
        <Table headers={["Componente", "Fuente", "Descripción"]} rows={[
          ["conf_base_table(count)", "Config (conf_base_0..4plus)", "Lookup table según cantidad de confluencias válidas"],
          ["quality_factor", "Hardcodeado (1.0)", "Reservado para futura calidad de señal — siempre 1.0 hoy"],
          ["regime_factor", "Calibración + dirección", "Multiplicador direccional según régimen de mercado"],
          ["confidence_adjustment", "LLM (±0.10 max)", "Ajuste subjetivo del LLM con justificación en texto"],
        ]} />
      </Card>

      {/* ───── 6. REGIMEN FACTOR ───── */}
      <Card title="6. Factor de Régimen (Regime Factor)">
        <p className="text-zinc-400 text-sm mb-3">
          Multiplicador direccional que ajusta la confianza base según el régimen de mercado que el LLM declara.
          El LLM tiene autonomía total para comer contra el régimen (ej. BUY en TRENDING_DOWN) con justificación.
        </p>
        <Table headers={["Régimen", "Dirección LONG", "Dirección SHORT"]} rows={[
          ["TRENDING_UP", "1.0", "0.0"],
          ["TRENDING_DOWN", "0.0", "1.0"],
          ["RANGE", `peso_regime_range (${"1.0"})`, `peso_regime_range (${"1.0"})`],
          ["HIGH_VOLATILITY", `peso_regime_high_vol (${"0.75"})`, `peso_regime_high_vol (${"0.75"})`],
          ["NEUTRAL", `peso_regime_range (${"1.0"})`, `peso_regime_range (${"1.0"})`],
        ]} />
        <p className="text-zinc-500 text-xs mt-2">
          El LLM declara el campo <Code>regime</Code> (enum). Para HOLD en futuros con TRENDING_DOWN,
          se usa dirección SHORT vía <Code>hold_signal_direction()</Code> para evitar confidence_base=0% espuria.
        </p>
      </Card>

      {/* ───── 7. POSITION SIZING ───── */}
      <Card title="7. Position Sizing (Tamaño de Posición)">
        <p className="text-zinc-400 text-sm mb-3">
          El servidor <strong>recalcula</strong> el tamaño de posición de forma determinística.
          La propuesta del LLM se ignora para ejecución (solo se guarda en <Code>position_size_meta</Code> para auditoría).
        </p>
        <div className="bg-zinc-950 rounded-lg p-4 border border-zinc-700 mb-3 text-center">
          <code className="text-emerald-300 text-sm font-mono">
            position_size_pct = risk_per_trade_pct / sl_distance_pct
          </code>
        </div>
        <ul className="text-sm text-zinc-400 space-y-2">
          <li><Badge color="sky">risk_per_trade_pct</Badge> — Riesgo objetivo por trade como fracción del capital (default 0.5%)</li>
          <li><Badge color="sky">sl_distance_pct</Badge> — Distancia del entry al Stop Loss como % del precio</li>
          <li><Badge color="zinc">Cap</Badge> por <Code>max_position_pct</Code> (R1) y piso por <Code>min_position_size</Code></li>
          <li><Badge color="zinc">HOLD/SELL</Badge> → position_size_pct forzado a 0.0</li>
        </ul>
      </Card>

      {/* ───── 8. COHERENCE CHECKER ───── */}
      <Card title="8. Coherence Checker (C1–C10)">
        <p className="text-zinc-400 text-sm mb-3">
          Validaciones internas que detectan inconsistencias entre lo que el LLM declara y los datos reales del mercado.
          Las reglas C7 siempre bloquean. Las demás son warnings que en <Code>strict_mode</Code> se vuelven críticas.
          Si <Code>two_pass_enabled</Code> (default true), warnings C1/C2/C3/C1P/C2P/C3P/C7/C9/C10 gatillan
          una segunda llamada al LLM para auto-corrección.
        </p>
        <Table headers={["Regla", "Qué Valida", "Severidad Default"]} rows={[
          ["C1", "Confluencia A (RSI oversold) sin RSI&lt;35", "warning (crítica en strict_mode)"],
          ["C2", "Confluencia B (MACD bullish cross) sin MACD&gt;Signal", "warning (crítica en strict_mode)"],
          ["C3", "Régimen vs EMAs/ADX inconsistente", "warning (crítica en strict_mode)"],
          ["C1P", "Confluencia I (RSI overbought) sin RSI&gt;65", "warning (crítica en strict_mode)"],
          ["C2P", "Confluencia J (MACD bearish cross) sin MACD&lt;Signal", "warning (crítica en strict_mode)"],
          ["C3P", "SHORT action con régimen/indicadores incoherentes", "warning (crítica en strict_mode)"],
          ["C4", "Confianza ≥0.85 con &lt;2 confluencias", "warning"],
          ["C5", "BUY con confianza &lt;0.60 sin tag explicativo", "warning"],
          ["C5P", "SHORT con confianza &lt;0.50 sin tag", "warning"],
          ["C6", "expected_holding_min fuera de rango del perfil", "warning"],
          ["C7", "R:R real ≤ min_rr_ratio", "crítica (siempre bloquea)"],
          ["C8", "K–Z declarada pero verify_spec falla", "warning"],
          ["C9", "Mix bull+bear opuestas o tag direccional vs acción", "warning (crítica en strict_mode)"],
          ["C10", "TP proyectado más allá del rango 24h sin justificación", "warning (crítica si falta [TP_PROYECTADO])"],
        ]} />
      </Card>

      {/* ───── 9. RISK GATE ───── */}
      <Card title="9. Risk Gate (R0–R15)">
        <p className="text-zinc-400 text-sm mb-3">
          El Risk Gate es la <strong>única barrera dura</strong> (<em>hard blocking</em>) que puede impedir un trade.
          Se ejecuta después del CoherenceChecker y antes de la ejecución. Cualquier regla que falle
          rechaza la decisión y persiste <Code>rejected_reason</Code>.
        </p>
        <Table headers={["ID", "Regla", "Comportamiento"]} rows={[
          ["R0_drawdown", "drawdown total &gt; max_drawdown_pct", "Rechaza si se excedió el drawdown máximo"],
          ["R0_kill_switch", "kill_switch activo y no es SELL", "Solo SELL permitido con kill switch on"],
          ["R1", "position_size_pct ≤ max_position_pct", "Rechaza posiciones sobredimensionadas"],
          ["R2", "SL del lado correcto (LONG: SL&lt;price, SHORT: SL&gt;price)", "Rechaza SL mal ubicado"],
          ["R3", "TP del lado correcto (LONG: TP&gt;price, SHORT: TP&lt;price)", "Rechaza TP mal ubicado"],
          ["R4", "Distancia SL en [sl_atr_mult×ATR, sl_atr_max_mult×ATR]", "Rechaza SL fuera de bandas ATR"],
          ["R5", "reward/risk ≥ min_rr_ratio", "Rechaza mala relación riesgo/beneficio"],
          ["R6", "SELL requiere posición abierta", "Rechaza SELL sin posición"],
          ["R7", "SHORT solo si trading_product=futures", "Rechaza SHORT en spot"],
          ["R8", "open_positions &lt; max_simultaneous_trades", "Rechaza si ya hay máximas posiciones"],
          ["R9", "daily_pnl_pct &gt; daily_stop_pct", "Rechaza si se alcanzó el límite diario"],
          ["R10", "TP cubre min_fees_to_tp_ratio × fees + slippage", "Rechaza si TP no cubre costos"],
          ["R11", "Entry/SL/TP notional ≥ min_notional_usdt", "Rechaza posiciones sub-mínimas"],
          ["R12", "leverage ≤ max_leverage", "Rechaza apalancamiento excesivo"],
          ["R13", "Liquidación ≥ liquidation_buffer_atr × ATR más allá del SL", "Rechaza si liquidación muy cerca del SL"],
          ["R14", "notional/leverage ≤ available_margin", "Rechaza margen insuficiente"],
          ["R15", "|funding_rate| ≤ funding_rate_max_pct", "Rechaza funding rate extremo"],
        ]} />
      </Card>

      {/* ───── 10. CAPAS DE DEFENSA ───── */}
      <Card title="10. Capas de Defensa">
        <p className="text-zinc-400 text-sm mb-3">
          El sistema implementa 6 capas de defensa en orden secuencial. Cada capa puede detener
          una decisión antes de que llegue a ejecución.
        </p>
        <div className="space-y-1.5 text-sm">
          {[
            ["Capa 1", "LLM Decisor", "Decisión autónoma del modelo de lenguaje", "emerald"],
            ["Capa 2a", "Pydantic Validation", "Validación estructural del JSON de salida", "zinc"],
            ["Capa 2b", "Server-Side Confidence", "Recálculo de confidence_base en servidor", "sky"],
            ["Capa 2c", "Early-Exit", "HOLD automático si indicadores críticos son null", "amber"],
            ["Capa 2d", "Risk-Based Sizing", "Recálculo determinístico del tamaño de posición", "sky"],
            ["Capa 3", "Coherence Checker", "C1–C10: inconsistencias entre declaración y datos", "purple"],
            ["Capa 4", "Risk Gate", "R0–R15: barrera dura que rechaza trades", "red"],
            ["Capa 5", "Circuit Breaker", "Pausa el motor ante fallos en cadena", "amber"],
            ["Capa 6", "Operador", "Kill switch manual, rollback, strict_mode", "red"],
          ].map(([layer, name, desc, color]) => (
            <div key={name} className="flex items-center gap-3 rounded-lg bg-zinc-900/50 p-3 border border-zinc-800">
              <Badge color={color}>{layer}</Badge>
              <div>
                <span className="font-medium text-zinc-200">{name}</span>
                <span className="text-zinc-500 text-xs ml-2">{desc}</span>
              </div>
            </div>
          ))}
        </div>

        <SubTitle>Circuit Breaker</SubTitle>
        <p className="text-zinc-400 text-sm">
          Dos tipos de pausa:
        </p>
        <ul className="text-sm text-zinc-400 space-y-1 mt-1">
          <li><Badge color="amber">Operacional</Badge> — Fallos de LLM o exchange. Auto-reset tras 10 min sin nuevos fallos.</li>
          <li><Badge color="red">Financiera</Badge> — daily_stop o max_drawdown alcanzado. Requiere reset manual del operador.</li>
        </ul>
        <p className="text-zinc-500 text-xs mt-2">
          5 fallos consecutivos de LLM → pausa. 5 fallos consecutivos de exchange → pausa.
        </p>
      </Card>

      {/* ───── 11. OUTCOME ATTRIBUTION ───── */}
      <Card title="11. Outcome Attribution y Post-Mortem">
        <p className="text-zinc-400 text-sm mb-3">
          El sistema no solo registra decisiones, sino que <strong>evalúa sus resultados</strong> para
          retroalimentar el aprendizaje.
        </p>

        <SubTitle>Outcome Attribution</SubTitle>
        <p className="text-zinc-400 text-sm mb-2">
          Job periódico (default cada 60 min) que analiza velas 1m post-decisión para calcular
          MFE/MAE (<em>Maximum Favorable/Adverse Excursion</em>) y clasifica cada decisión como:
        </p>
        <div className="flex flex-wrap gap-2 mb-3">
          {["WIN", "LOSS", "UNKNOWN"].map(s => <Badge key={s} color={s === "WIN" ? "emerald" : s === "LOSS" ? "red" : "zinc"}>{s}</Badge>)}
        </div>
        <p className="text-zinc-500 text-xs">
          También computa <Code>counterfactual_win</Code> (¿habría ganado si hubiera hecho lo contrario?),
          <Code>direction_accuracy</Code> (precisión direccional) y contadores de confluencias.
        </p>

        <SubTitle>Post-Mortem</SubTitle>
        <p className="text-zinc-400 text-sm mb-2">
          Analiza decisiones con outcome negativo vía LLM, extrayendo lecciones que se inyectan
          en el <strong>Bloque K</strong> del contexto del Decisor para evitar errores similares.
        </p>
        <ul className="text-sm text-zinc-400 space-y-1">
          <li><Badge color="purple">Lesson Normalizer</Badge> — Convierte lecciones en candidatos a confluencia (K–Z)</li>
          <li><Badge color="zinc">Bloque K</Badge> — Máximo <Code>block_k_max_lines</Code> (default 5) lecciones visibles al Decisor</li>
        </ul>
      </Card>

      {/* ───── 12. LLM PROVIDERS ───── */}
      <Card title="12. Proveedores LLM (Cascada)">
        <p className="text-zinc-400 text-sm mb-3">
          Cada agente usa una cascada de proveedores con fallback automático.
          Rate limits (429/ResourceExhausted) saltan al siguiente proveedor sin reintentar.
          Otros errores reintentan hasta <Code>llm_max_retries</Code> veces con backoff exponencial.
        </p>
        <Table headers={["Agente", "Provider Primario", "Fallbacks"]} rows={[
          ["Decisor", "groq-llama-3.3-70b", "gemini-2.5-flash, groq-llama-4-scout, groq-gpt-oss-120b, groq-qwen3-32b, groq-llama-3.1-8b, ollama-deepseek-v4-flash, ollama-qwen3.5-32b"],
          ["Supervisor", "gemini-2.5-pro", "groq-llama-3.3-70b, groq-llama-4-scout, groq-gpt-oss-120b, gemini-2.5-flash, ollama-deepseek-v4-pro"],
          ["Post-Mortem", "gemini-2.5-flash", "groq-compound-mini, groq-llama-4-scout, groq-qwen3-32b, groq-gpt-oss-20b, groq-llama-3.1-8b, ollama-kimi-k2-thinking"],
        ]} />
      </Card>

      {/* ───── 13. MODO FUTUROS ───── */}
      <Card title="13. Modo Futuros">
        <p className="text-zinc-400 text-sm mb-3">
          Cuando <Code>trading_product=futures</Code>, se habilitan funcionalidades adicionales:
        </p>
        <ul className="text-sm text-zinc-400 space-y-2">
          <li><Badge color="emerald">SHORT</Badge> — Órdenes de venta en corto (con SL y TP invertidos)</li>
          <li><Badge color="amber">Confluencias I–J</Badge> — Señales bajistas RSI_OVERBOUGHT_REJECTION y MACD_BEARISH_CROSS</li>
          <li><Badge color="red">Risk Gates R7, R12–R15</Badge> — Validaciones específicas de futuros</li>
          <li><Badge color="sky">Apalancamiento</Badge> — Configurable vía <Code>max_leverage</Code> y <Code>margin_mode</Code></li>
          <li><Badge color="purple">Funding Rate</Badge> — Filtro <Code>funding_rate_max_pct</Code> (R15)</li>
          <li><Badge color="zinc">Liquidación</Badge> — Buffer <Code>liquidation_buffer_atr</Code> entre SL y liquidación (R13)</li>
        </ul>
        <p className="text-zinc-500 text-xs mt-2">
          Si al arrancar con <Code>trading_product=futures</Code> hay problemas de configuración,
          el sistema hace downgrade automático a spot y registra el motivo en <Code>futures_runtime_downgrade_reason</Code>.
        </p>
      </Card>

    </div>
  );
}
