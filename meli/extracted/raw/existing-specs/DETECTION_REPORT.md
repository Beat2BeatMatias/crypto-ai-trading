# Detection Report

**Generated**: 2026-05-14
**Repository**: crypto-ai-trading

## Extraction Scope

- **Mode**: FULL EXTRACTION
- **Optimization strategy**: ASSISTED
- **FuryMCP / MeliSystemMCP**: NOT APPLICABLE — proyecto personal, no es una app Fury de Mercado Libre

## Detected Frameworks

| Framework | Confidence | Files Found |
|-----------|------------|-------------|
| Claude Code (CLAUDE.md) | 🟡 Medium | `CLAUDE.md` |
| Plain Docs — Design Doc | 🟢 High | `docs/superpowers/specs/2026-05-02-crypto-ai-trading-design.md` (1046 líneas) |
| Plain Docs — Implementation Plan | 🟢 High | `docs/superpowers/plans/2026-05-02-crypto-ai-trading.md` (7636 líneas) |
| Plain Docs — Decisor v2 Refactor Plan | 🟢 High | `docs/superpowers/plans/2026-05-12-decisor-v2-refactor.md` (1271 líneas) |
| Plain Docs — Prompt history | 🟡 Medium | `docs/prompts.md` (315 líneas) |
| Meli SDD Kit | ❌ None | — |
| OpenSpec / Spec-Kit / Kiro / Tessl | ❌ None | — |
| Cursor Rules | ❌ None | `.cursor/` vacío |
| OpenAPI / Swagger | ❌ None | — |
| ADR / RFC | ❌ None | — |

## Selected Strategy: ASSISTED

Existe un design doc extenso y muy detallado (1046 líneas) además de planes de implementación voluminosos. Estos documentos se tratan como **hints válidos** pero el código permanece como fuente de verdad. La estrategia ASSISTED implica:

1. Cargar los docs existentes en `existing-specs/` para referencia.
2. Análisis exhaustivo del código en todas las capas.
3. Cross-validar docs vs código y marcar discrepancias.
4. En caso de conflicto, el código manda.

## Detected Specs Summary

| Spec Type | Location | Last Modified | Líneas |
|-----------|----------|---------------|--------|
| Design Doc | `docs/superpowers/specs/2026-05-02-crypto-ai-trading-design.md` | 2026-05-02 | 1046 |
| Implementation Plan | `docs/superpowers/plans/2026-05-02-crypto-ai-trading.md` | 2026-05-02 | 7636 |
| Decisor v2 Refactor Plan | `docs/superpowers/plans/2026-05-12-decisor-v2-refactor.md` | 2026-05-12 | 1271 |
| Prompt history | `docs/prompts.md` | 2026-05-13 | 315 |
| README operativo | `README.md` | 2026-05-09 | 364 |

## Extraction History

| Date | Mode | Focus | Summary |
|------|------|-------|---------|
| 2026-05-14 | FULL | Full monorepo | Initial extraction |

## Recommendations

- Cross-validar el design doc (2026-05-02) contra la implementación real (drift de 12 días).
- Validar si el plan "Decisor v2 Refactor" del 2026-05-12 fue ejecutado.
- Documentar las discrepancias en `DISCREPANCIES_REPORT.md`.
