import { useEffect, useState } from "react";
import { api } from "../api/client";

export function useLiveSinceFilter() {
  const [liveSinceIso, setLiveSinceIso] = useState<string | null>(null);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [includePaper, setIncludePaper] = useState(false);

  useEffect(() => {
    api.config().then(entries => {
      const mode = entries.find(e => e.key === "mode")?.value;
      const since = entries.find(e => e.key === "live_since_ts")?.value?.trim();
      if (mode === "LIVE" && since) {
        setLiveSinceIso(since);
        setDateFrom(since.slice(0, 10));
      }
    }).catch(() => {});
  }, []);

  function clearDateFilters() {
    setDateFrom("");
    setDateTo("");
    setIncludePaper(true);
  }

  const filteringActive =
    Boolean(dateTo) ||
    Boolean(dateFrom) ||
    (Boolean(liveSinceIso) && !includePaper);

  return {
    liveSinceIso,
    dateFrom,
    setDateFrom,
    dateTo,
    setDateTo,
    includePaper,
    setIncludePaper,
    clearDateFilters,
    hasCustomDateFilter: filteringActive,
  };
}
