export function asDecisorOutput(output) {
    return output;
}
export function fmtConfidencePct(value) {
    const n = typeof value === "number" ? value : 0;
    return `${(n * 100).toLocaleString("es-AR", { maximumFractionDigits: 0 })}%`;
}
