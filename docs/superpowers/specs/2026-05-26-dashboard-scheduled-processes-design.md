# Dashboard: Countdown de Procesos Programados + Header

**Fecha:** 2026-05-26  
**Estado:** Aprobado

---

## Problema

El dashboard solo muestra el countdown al próximo Decisor. Existen 7 procesos schedulados en el trading-engine; el usuario quiere ver las próximas ejecuciones de los 4 procesos clave. Además, el dashboard carece de título y descripción contextual.

## Diseño Elegido

### Layout (aprobado en brainstorming visual)
- **Header**: opción B — Título "Dashboard" + par BTC/USDT inline (verde), descripción en gris, chips de contexto (Par · Modo · Exchange).
- **Tabla de procesos**: opción C — tabla con 4 filas (Decisor, Supervisor, Outcome Attribution, Fees), columnas: Proceso · Próxima ejecución (countdown) · Intervalo · Descripción.

---

## Arquitectura

```
trading-engine (APScheduler)
    → job.next_run_time (por job)
    → upsert en scheduler_status (Postgres) al inicio de cada job

web /api/health
    → lee scheduler_status
    → incluye scheduled_processes[] en el response JSON

Frontend Dashboard (polling 15s, ya existente)
    → recibe scheduled_processes[] del health endpoint
    → countdown local por proceso (setInterval 1s)
```

**Principio:** Postgres es la única fuente de verdad entre los dos procesos. No hay IPC directo.

---

## Backend — trading-engine

### Nueva tabla `scheduler_status`

```sql
CREATE TABLE scheduler_status (
    job_id        VARCHAR(64)   PRIMARY KEY,
    name          VARCHAR(128)  NOT NULL,
    next_run_at   TIMESTAMPTZ,
    last_run_at   TIMESTAMPTZ,
    interval_desc VARCHAR(64)
);
```

### Cambios en `scheduler.py`

- Agregar método `_update_job_status(job_id: str)` que hace upsert en `scheduler_status` usando `scheduler.get_job(job_id).next_run_time`.
- Llamar `_update_job_status` al inicio de cada job (wrapper o listener de APScheduler).
- Poblar la tabla en startup con los 4 jobs clave: `decisor`, `supervisor`, `outcome_attribution`, `fees`.

### Migración Alembic

Un archivo de migración que crea `scheduler_status` e inserta las 4 filas iniciales con `interval_desc` y `name`.

---

## Backend — web

### Cambios en `web/api/health.py`

Agregar al response de `/api/health`:

```json
"scheduled_processes": [
  {
    "id": "decisor",
    "name": "Decisor",
    "next_run_at": "2026-05-26T14:32:00Z",
    "last_run_at": "2026-05-26T14:22:00Z",
    "interval_desc": "~10 min"
  },
  {
    "id": "supervisor",
    "name": "Supervisor",
    "next_run_at": "2026-05-27T00:00:00Z",
    "last_run_at": "2026-05-26T00:00:00Z",
    "interval_desc": "diario"
  },
  {
    "id": "outcome_attribution",
    "name": "Outcome",
    "next_run_at": "2026-05-26T15:10:00Z",
    "last_run_at": "2026-05-26T14:10:00Z",
    "interval_desc": "60 min"
  },
  {
    "id": "fees",
    "name": "Fees",
    "next_run_at": "2026-05-27T14:22:00Z",
    "last_run_at": "2026-05-26T14:22:00Z",
    "interval_desc": "24 h"
  }
]
```

Si la tabla está vacía (trading-engine no corrió aún), devuelve array vacío.

---

## Frontend

### Tipo `EngineHealth` (`types/index.ts`)

Agregar:
```typescript
scheduled_processes?: Array<{
  id: string;
  name: string;
  next_run_at: string | null;  // ISO UTC
  last_run_at: string | null;
  interval_desc: string;
}>;
```

### Hook `useProcessCountdowns`

Nuevo hook en `Dashboard.tsx` (o archivo propio si se prefiere). Recibe `scheduled_processes[]` y devuelve un mapa `{ [job_id]: secsLeft | null }`. Usa `setInterval(1s)` y recalcula cuando cambia `next_run_at`.

```typescript
function useProcessCountdowns(
  processes: ScheduledProcess[]
): Record<string, number | null>
```

### Componente `DashboardHeader`

```tsx
<DashboardHeader
  pair="BTC/USDT"
  mode={config?.trading_mode ?? "PAPER_TRADING"}
  exchange="Binance"
/>
```

Muestra: título "Dashboard", par inline en verde, descripción, chips Par/Modo/Exchange.

### Componente `ScheduledProcessesTable`

```tsx
<ScheduledProcessesTable
  processes={engineHealth?.scheduled_processes ?? []}
  countdowns={processCountdowns}
/>
```

Tabla con 4 filas: columnas Proceso · Próxima ejecución (countdown formateado) · Intervalo · Descripción.  
Formato de countdown:
- `> 60 min` → `"Xh Ym"` (zinc)
- `1–60 min` → `"Xm Ys"` (azul)
- `< 60s` → `"Xs"` (ámbar pulsante)
- `0s` → `"ejecutando..."` (esmeralda)

### Posición en Dashboard

```
[DashboardHeader]          ← nuevo
[EngineStatusPill]         ← existente (sin cambios)
[ScheduledProcessesTable]  ← nuevo
[PriceChart]               ← existente (sin cambios)
[Balance + Positions + Decision + Stats]  ← existentes
```

---

## Consideraciones

- El countdown del Decisor existente en el status pill **no se elimina** — la tabla complementa, no reemplaza.
- Si `scheduled_processes` llega vacío (trading-engine offline), la tabla no se renderiza.
- Compatibilidad: `next_run_at` puede ser `null` si APScheduler aún no scheduló el job (paused/disabled).
- Tests: el hook `useProcessCountdowns` es puro y testeable con fechas fijas (freezegun en backend, timestamps fijos en frontend).
