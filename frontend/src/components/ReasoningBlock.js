import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
// Tags conocidos del formato de reasoning del LLM.
// Las claves están normalizadas (sin tildes, sin espacios) para hacer lookup
// independiente de cómo el LLM escriba el tag exacto.
const TAG_CONFIG = {
    "REVISADO-MANTENIDO": { label: "Revisado · Mantenido", color: "#facc15", bg: "#451a03", icon: "↩" },
    "DATOS_INSUFICIENTES": { label: "Datos insuficientes", color: "#f97316", bg: "#431407", icon: "⚠" },
    "DECISION": { label: "Decisión", color: "#e2e8f0", bg: "#1e293b", icon: "▶" },
    "MERCADO": { label: "Mercado", color: "#38bdf8", bg: "#082f49", icon: "📈" },
    "SENALES": { label: "Señales", color: "#34d399", bg: "#052e16", icon: "⚡" },
    "CONFIANZA": { label: "Confianza", color: "#a78bfa", bg: "#2e1065", icon: "%" },
    "NIVELES": { label: "Niveles SL/TP", color: "#fb923c", bg: "#431407", icon: "⚖" },
    "CONTRA_REGIMEN": { label: "Contra régimen", color: "#f87171", bg: "#450a0a", icon: "⚠" },
    "BAJA_CONFIANZA": { label: "Baja confianza", color: "#fbbf24", bg: "#451a03", icon: "⚠" },
    "SIZING": { label: "Sizing", color: "#94a3b8", bg: "#1e293b", icon: "⚖" },
    "DRIFT_CONFIG": { label: "Drift config", color: "#f472b6", bg: "#4a044e", icon: "⚙" },
};
/**
 * Normaliza el texto de un tag para hacer lookup en TAG_CONFIG.
 * - Elimina tildes/diacríticos (Ó→O, Ñ→N, É→E, etc.)
 * - Convierte a mayúsculas
 * - Reemplaza espacios por guion bajo
 */
function normalizeTag(raw) {
    return raw
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "") // quita diacríticos
        .toUpperCase()
        .replace(/\s+/g, "_");
}
function parseReasoning(raw) {
    // Captura [TAG] donde TAG puede contener letras (incl. tildes/Ñ), guiones, guiones bajos y espacios.
    // El lookahead detecta el próximo [TAG] para delimitar el contenido de cada sección.
    const pattern = /\[([\p{L}\p{M}0-9 _\-]+)\]\s*(.*?)(?=\[[\p{L}\p{M}0-9 _\-]+\]|$)/gsu;
    const sections = [];
    let match;
    while ((match = pattern.exec(raw)) !== null) {
        const tag = normalizeTag(match[1].trim());
        const content = match[2].trim();
        if (content) {
            sections.push({ tag, content });
        }
    }
    // Si no encontró ninguna sección con tags, devolver el texto completo como bloque genérico
    if (sections.length === 0 && raw.trim()) {
        sections.push({ tag: "__raw__", content: raw.trim() });
    }
    return sections;
}
const ReasoningBlock = ({ reasoning, compact = false }) => {
    if (!reasoning?.trim())
        return null;
    const sections = parseReasoning(reasoning);
    if (sections.length === 1 && sections[0].tag === "__raw__") {
        // Sin tags reconocidos: mostrar como texto simple
        return (_jsx("p", { className: "leading-relaxed", style: { color: "#c9d1d9", fontSize: compact ? 11 : 13, fontStyle: "italic" }, children: sections[0].content }));
    }
    return (_jsx("div", { className: `flex flex-col ${compact ? "gap-1" : "gap-2"}`, children: sections.map((s, i) => {
            const cfg = TAG_CONFIG[s.tag];
            const label = cfg?.label ?? s.tag;
            const color = cfg?.color ?? "#94a3b8";
            const bg = cfg?.bg ?? "#1e293b";
            const icon = cfg?.icon ?? "·";
            return (_jsxs("div", { style: {
                    background: bg,
                    borderLeft: `2px solid ${color}`,
                    borderRadius: 4,
                    padding: compact ? "4px 8px" : "6px 10px",
                }, children: [_jsxs("div", { style: {
                            color,
                            fontSize: compact ? 9 : 10,
                            fontWeight: 700,
                            textTransform: "uppercase",
                            letterSpacing: "0.08em",
                            marginBottom: 2,
                            display: "flex",
                            alignItems: "center",
                            gap: 4,
                        }, children: [_jsx("span", { children: icon }), _jsx("span", { children: label })] }), _jsx("p", { style: {
                            color: "#cbd5e1",
                            fontSize: compact ? 11 : 12,
                            lineHeight: 1.5,
                            margin: 0,
                        }, children: s.content })] }, i));
        }) }));
};
export default ReasoningBlock;
