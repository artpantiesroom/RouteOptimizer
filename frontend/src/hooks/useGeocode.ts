import { useState, useRef } from "react";
import { geocodeBatch, parse, type GeocodeBatchResponseItem, type GeocodeItem } from "../api/client";
import type {
  ErrorKind,
  GeocodeResultItem,
  GeocodeStatus,
  ParsedItem,
  UnitKind,
} from "../types/types";

// User-facing copy. Must stay free of technical terms (geocoding, provider, HTTP).
// Rendered by ResultItem, which owns the wording shown to the user.
const RETRYABLE_ERROR_MESSAGE = "Could not check this address right now. Try again.";
const OFFLINE_ERROR_MESSAGE =
  "Could not reach the address search. Your edit is kept, try again when you are back online.";

export interface UseGeocodeState {
  items: GeocodeResultItem[];
  total: number;
  processed: number;
  loading: boolean;
  error: string | null;
  /** Scope applied to every row. Prefilled from the first address. */
  city: string;
  /**
   * City suggested by the resolved rows when the City field was blank. Shown
   * as a hint only: it is never applied unless the user accepts it.
   */
  citySuggestion: string | null;
  /** Share of confident rows behind the suggestion, 0-1. */
  citySuggestionShare: number | null;
}

/** Fold a backend result onto a row, clearing any previous answer. */
function mergeResult(item: GeocodeResultItem, r: GeocodeBatchResponseItem): GeocodeResultItem {
  return {
    ...item,
    status: (r.status ?? "error") as GeocodeStatus,
    coordinate: r.coordinate,
    display_name: r.display_name,
    candidates: r.candidates,
    error_message: r.error_message ?? null,
    message: r.message ?? null,
    error_kind: (r.error_kind as ErrorKind | undefined) ?? undefined,
    searched_as: r.searched_as ?? null,
    house: r.house ?? null,
    unit: r.unit ?? null,
    unit_kind: (r.unit_kind as UnitKind | undefined) ?? null,
    unit_inferred: r.unit_inferred ?? false,
    dropped_candidates: r.dropped_candidates ?? 0,
    scope_message: r.scope_message ?? null,
    found_house: r.found_house ?? null,
    found_city: r.found_city ?? null,
    needs_check: r.needs_check ?? false,
    needs_check_reason: r.needs_check_reason ?? null,
    retry_city: r.retry_city ?? null,
    // A fresh answer puts the row back in play; a skipped row is only skipped
    // until the user re-includes it, edits it, or it is checked again.
    skipped: false,
    selectedCandidateIndex: undefined,
    confirmed: undefined,
    offline: false,
  };
}

export function useGeocode() {
  const [state, setState] = useState<UseGeocodeState>({
    items: [],
    total: 0,
    processed: 0,
    loading: false,
    error: null,
    city: "",
    citySuggestion: null,
    citySuggestionShare: null,
  });
  const abortControllerRef = useRef<AbortController | null>(null);
  const latestRunIdRef = useRef(0);
  const runBaseItemsRef = useRef<Map<number, GeocodeResultItem[]>>(new Map());

  function mapParsedToResult(p: ParsedItem): GeocodeResultItem {
    return {
      id: p.index,
      original: p.original,
      trimmed: p.trimmed,
      is_blank: p.is_blank,
      is_duplicate: p.is_duplicate,
      duplicate_of: p.duplicate_of ?? null,
      status: "not_found",
      skipped: false,
    } as GeocodeResultItem;
  }

  async function run(text: string) {
    const currentController = abortControllerRef.current;
    if (currentController) {
      currentController.abort();
    }
    const runId = ++latestRunIdRef.current;
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setState((prev) => ({
      ...prev,
      items: [],
      total: 0,
      processed: 0,
      loading: true,
      error: null,
      citySuggestion: null,
      citySuggestionShare: null,
    }));
    try {
      const parsed = await parse(text);
      if (runId !== latestRunIdRef.current) {
        return;
      }
      // Prefill the City field from the first address that names a known city,
      // but never over what the user already typed.
      const suggested = state.city.trim() ? state.city : parsed.suggested_city ?? "";
      if (suggested !== state.city) {
        setCity(suggested);
      }
      const results: GeocodeResultItem[] = parsed.items.map(mapParsedToResult);
      if (runId !== latestRunIdRef.current) {
        return;
      }
      runBaseItemsRef.current.set(runId, results.map((r) => ({ ...r })));
      setState((prev) => ({
        ...prev,
        items: results,
        total: parsed.non_blank,
        processed: 0,
        loading: true,
        error: null,
      }));

      const toProcess = results.filter((r) => !r.is_blank);
      let processedCount = 0;
      for (let i = 0; i < toProcess.length; i += 5) {
        if (runId !== latestRunIdRef.current) {
          return;
        }
        const batch = toProcess.slice(i, i + 5);
        const batchItems: GeocodeItem[] = batch.map((b) => ({
          index: b.id,
          original: b.original,
          trimmed: b.trimmed,
        }));
        let resp;
        try {
          resp = await geocodeBatch(batchItems, controller.signal, suggested);
        } catch (e: any) {
          if (e.name === "AbortError") {
            return;
          }
          throw e;
        }
        if (runId !== latestRunIdRef.current) {
          return;
        }
        processedCount += batch.length;
        // The suggestion is only meaningful once a whole batch of confident
        // rows agrees, so it replaces any earlier one rather than adding to it.
        const suggestion = resp.city_suggestion ?? null;
        setState((prev) => {
          if (runId !== latestRunIdRef.current) {
            return prev;
          }
          const base = runBaseItemsRef.current.get(runId) || prev.items;
          const next = base.map((item) => ({ ...item }));
          for (const r of resp.results) {
            const idx = next.findIndex((x) => x.id === r.index);
            if (idx >= 0) {
              next[idx] = mergeResult(next[idx], r);
            }
          }
          runBaseItemsRef.current.set(runId, next.map((n) => ({ ...n })));
          return {
            ...prev,
            items: next,
            processed: processedCount,
            citySuggestion: suggestion,
            citySuggestionShare: resp.city_suggestion_share ?? null,
          };
        });
      }
      if (runId !== latestRunIdRef.current) {
        return;
      }
      setState((prev) => {
        if (runId !== latestRunIdRef.current) {
          return prev;
        }
        return { ...prev, loading: false };
      });
    } catch (e: any) {
      if (e.name === "AbortError") {
        return;
      }
      if (runId !== latestRunIdRef.current) {
        return;
      }
      setState((prev) => {
        if (runId !== latestRunIdRef.current) {
          return prev;
        }
        return { ...prev, loading: false, error: e.message || "Unknown error" };
      });
    }
  }

  function selectCandidate(itemId: number, candidateIndex: number) {
    setState((prev) => {
      const next = [...prev.items];
      const idx = next.findIndex((x) => x.id === itemId);
      if (idx >= 0) {
        const item = next[idx];
        if (item.candidates && candidateIndex >= 0 && candidateIndex < item.candidates.length) {
          const c = item.candidates[candidateIndex];
          next[idx] = {
            ...item,
            selectedCandidateIndex: candidateIndex,
            status: "resolved",
            coordinate: { latitude: c.latitude, longitude: c.longitude },
            display_name: c.display_name,
          };
        }
      }
      return { ...prev, items: next };
    });
  }

  function confirmPartial(itemId: number) {
    setState((prev) => {
      const next = [...prev.items];
      const idx = next.findIndex((x) => x.id === itemId);
      if (idx >= 0) {
        next[idx] = { ...next[idx], confirmed: true };
      }
      return { ...prev, items: next };
    });
  }

  // Re-check a single row after a retryable failure. Only offered for
  // retryable errors: a setup error will not change on a second attempt.
  async function retryItem(itemId: number) {
    const item = state.items.find((i) => i.id === itemId);
    if (!item || item.retrying) return;
    if (item.error_kind !== "retryable") return;

    setState((prev) => ({
      ...prev,
      items: prev.items.map((i) =>
        i.id === itemId ? { ...i, retrying: true, error_message: null } : i
      ),
    }));

    try {
      const resp = await geocodeBatch(
        [{ index: item.id, original: item.original, trimmed: item.trimmed }],
        undefined,
        state.city
      );
      const r = resp.results[0];
      setState((prev) => ({
        ...prev,
        items: prev.items.map((i) =>
          i.id === itemId
            ? { ...mergeResult(i, r ?? ({ index: item.id, original: item.original, status: "error" } as GeocodeBatchResponseItem)), retrying: false }
            : i
        ),
      }));
    } catch {
      // Keep the row retryable so the user can try again.
      setState((prev) => ({
        ...prev,
        items: prev.items.map((i) =>
          i.id === itemId
            ? {
                ...i,
                retrying: false,
                status: "error",
                error_kind: "retryable",
                error_message: RETRYABLE_ERROR_MESSAGE,
              }
            : i
        ),
      }));
    }
  }

  /**
   * Edit one address in place and re-run just that row. The edited text
   * replaces the row's own text so the user always sees what was searched.
   */
  async function editItem(itemId: number, newText: string) {
    const trimmed = newText.trim();
    if (!trimmed) return;
    const existing = state.items.find((i) => i.id === itemId);
    if (!existing || existing.rechecking) return;

    setState((prev) => ({
      ...prev,
      items: prev.items.map((i) =>
        i.id === itemId
          ? { ...i, trimmed, original: trimmed, rechecking: true, error_message: null }
          : i
      ),
    }));

    try {
      const resp = await geocodeBatch(
        [{ index: itemId, original: trimmed, trimmed }],
        undefined,
        state.city
      );
      const r = resp.results[0];
      setState((prev) => ({
        ...prev,
        items: prev.items.map((i) =>
          i.id === itemId
            ? {
                ...mergeResult(
                  i,
                  r ?? ({ index: itemId, original: trimmed, status: "error" } as GeocodeBatchResponseItem)
                ),
                rechecking: false,
              }
            : i
        ),
      }));
    } catch {
      // Keep the edit so it is not lost; the user can run the row again.
      setState((prev) => ({
        ...prev,
        items: prev.items.map((i) =>
          i.id === itemId
            ? {
                ...i,
                rechecking: false,
                status: "error",
                error_kind: "retryable",
                offline: true,
                error_message: OFFLINE_ERROR_MESSAGE,
              }
            : i
        ),
      }));
    }
  }

  function setCity(city: string) {
    setState((prev) => ({ ...prev, city }));
  }

  /**
   * Set a row aside for this route (or bring it back). The row stays visible
   * and readable but takes no part in the calculation until it is included
   * again, fixed, or confirmed - nothing is dropped silently.
   */
  function toggleSkip(itemId: number) {
    setState((prev) => ({
      ...prev,
      items: prev.items.map((i) => (i.id === itemId ? { ...i, skipped: !i.skipped } : i)),
    }));
  }

  /**
   * Re-run only the rows that need a check, in the city they should be in.
   * Other rows keep their answers: they were not implicated.
   */
  async function retryFlaggedInCity(targetCity: string) {
    const city = targetCity.trim();
    if (!city) return;
    const flagged = state.items.filter(
      (i) => i.needs_check && !i.is_blank && !i.retrying && !i.rechecking
    );
    if (flagged.length === 0) return;

    const ids = new Set(flagged.map((i) => i.id));
    setState((prev) => ({
      ...prev,
      city,
      citySuggestion: null,
      citySuggestionShare: null,
      items: prev.items.map((i) =>
        ids.has(i.id) ? { ...i, retrying: true, error_message: null, needs_check: false } : i
      ),
    }));

    try {
      for (let i = 0; i < flagged.length; i += 5) {
        const batch = flagged.slice(i, i + 5);
        const resp = await geocodeBatch(
          batch.map((b) => ({ index: b.id, original: b.original, trimmed: b.trimmed })),
          undefined,
          city
        );
        setState((prev) => {
          const next = prev.items.map((item) => ({ ...item }));
          for (const r of resp.results) {
            const idx = next.findIndex((x) => x.id === r.index);
            if (idx >= 0) {
              next[idx] = { ...mergeResult(next[idx], r), retrying: false };
            }
          }
          return { ...prev, items: next };
        });
      }
    } catch {
      // Rows keep their text and stay editable, so the user can try again.
      setState((prev) => ({
        ...prev,
        items: prev.items.map((i) =>
          ids.has(i.id)
            ? {
                ...i,
                retrying: false,
                status: "error",
                error_kind: "retryable",
                error_message: RETRYABLE_ERROR_MESSAGE,
              }
            : i
        ),
      }));
    }
  }

  return {
    state,
    run,
    selectCandidate,
    confirmPartial,
    retryItem,
    editItem,
    setCity,
    retryFlaggedInCity,
    toggleSkip,
  };
}