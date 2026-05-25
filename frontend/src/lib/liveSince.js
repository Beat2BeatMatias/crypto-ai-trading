export function cutoffFromDateInput(dateFrom, liveSinceIso) {
    if (liveSinceIso && dateFrom === liveSinceIso.slice(0, 10)) {
        return new Date(liveSinceIso);
    }
    return new Date(dateFrom);
}
