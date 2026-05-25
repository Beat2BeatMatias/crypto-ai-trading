export function cutoffFromDateInput(dateFrom: string, liveSinceIso: string | null): Date {
  if (liveSinceIso && dateFrom === liveSinceIso.slice(0, 10)) {
    return new Date(liveSinceIso);
  }
  return new Date(dateFrom);
}
