import { useState, useRef } from "react";
import { geocodeBatch, parse, type GeocodeItem } from "../api/client";
import type {
  ErrorKind,
  GeocodeResultItem,
  GeocodeStatus,
  ParsedItem,
} from "../types/types";

// User-facing copy. Must stay free of technical terms (geocoding, provider, HTTP).
// Rendered by ResultItem, which owns the wording shown to the user.
const RETRYABLE_ERROR_MESSAGE = "Could not check this address right now. Try again.";

export interface UseGeocodeState {
  items: GeocodeResultItem[];
  total: number;
  processed: number;
  loading: boolean;
  error: string | null;
}

export function useGeocode() {
  const [state, setState] = useState<UseGeocodeState>({
    items: [],
    total: 0,
    processed: 0,
    loading: false,
    error: null,
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

    setState({ items: [], total: 0, processed: 0, loading: true, error: null });
    try {
      const parsed = await parse(text);
      if (runId !== latestRunIdRef.current) {
        return;
      }
      const results: GeocodeResultItem[] = parsed.items.map(mapParsedToResult);
      if (runId !== latestRunIdRef.current) {
        return;
      }
      runBaseItemsRef.current.set(runId, results.map((r) => ({ ...r })));
      setState({
        items: results,
        total: parsed.non_blank,
        processed: 0,
        loading: true,
        error: null,
      });

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
          resp = await geocodeBatch(batchItems, controller.signal);
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
        setState((prev) => {
          if (runId !== latestRunIdRef.current) {
            return prev;
          }
          const base = runBaseItemsRef.current.get(runId) || prev.items;
          const next = base.map((item) => ({ ...item }));
          for (const r of resp.results) {
            const idx = next.findIndex((x) => x.id === r.index);
            if (idx >= 0) {
              next[idx] = {
                ...next[idx],
                status: r.status as GeocodeStatus,
                coordinate: r.coordinate,
                display_name: r.display_name,
                candidates: r.candidates,
                error_message: r.error_message,
                message: r.message,
                error_kind: r.error_kind as ErrorKind | undefined,
              };
            }
          }
          runBaseItemsRef.current.set(runId, next.map((n) => ({ ...n })));
          return { ...prev, items: next, processed: processedCount };
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
      const resp = await geocodeBatch([
        { index: item.id, original: item.original, trimmed: item.trimmed },
      ]);
      const r = resp.results[0];
      setState((prev) => ({
        ...prev,
        items: prev.items.map((i) =>
          i.id === itemId
            ? {
                ...i,
                retrying: false,
                status: (r?.status ?? "error") as GeocodeStatus,
                coordinate: r?.coordinate,
                display_name: r?.display_name,
                candidates: r?.candidates,
                error_message: r?.error_message ?? null,
                message: r?.message ?? null,
                error_kind: (r?.error_kind as ErrorKind | undefined) ?? undefined,
              }
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

  return { state, run, selectCandidate, confirmPartial, retryItem };
}
