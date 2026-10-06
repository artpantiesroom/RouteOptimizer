import { useState, useRef } from "react";
import { geocodeBatch } from "../api/client";
import type { GeocodeCandidate } from "../types/types";

/**
 * The pre-geocoder for a single start-point address: same recognition rules as
 * a row (resolved / partial with Confirm / ambiguous with pick), but the result
 * is one coordinate used as matrix row 0.
 */

export type StartStatus = "idle" | "resolving" | "resolved" | "partial" | "ambiguous" | "not_found" | "error";

export interface StartPointState {
  text: string;
  status: StartStatus;
  coordinate?: { latitude: number; longitude: number };
  display_name?: string;
  candidates?: GeocodeCandidate[];
  message: string | null;
}

const NOT_FOUND_MESSAGE = "Could not find this address. Try a different phrasing.";
const RETRYABLE_MESSAGE = "Could not check this address right now. Try again.";
const SETUP_MESSAGE =
  "The address search service rejected the request. This is a setup problem, not your address.";

function fold(r: StartPointState, next: Partial<StartPointState>): StartPointState {
  return { ...r, ...next };
}

export function useStartPoint() {
  const [state, setState] = useState<StartPointState>({
    text: "",
    status: "idle",
    message: null,
  });
  const runIdRef = useRef(0);

  function setText(text: string) {
    runIdRef.current += 1; // invalidate any in-flight lookup
    setState({ text, status: "idle", message: null });
  }

  async function run(city?: string | null) {
    const trimmed = state.text.trim();
    if (!trimmed) return;
    const runId = ++runIdRef.current;
    setState((prev) => ({ ...prev, text: trimmed, status: "resolving", message: null }));
    try {
      const resp = await geocodeBatch(
        [{ index: 0, original: trimmed, trimmed }],
        undefined,
        city ?? null
      );
      if (runId !== runIdRef.current) return;
      const r = resp.results[0];
      if (!r) {
        setState((prev) => fold(prev, { status: "error", message: NOT_FOUND_MESSAGE }));
        return;
      }
      if (r.status === "resolved" && r.coordinate) {
        setState((prev) =>
          fold(prev, {
            status: "resolved",
            coordinate: r.coordinate,
            display_name: r.display_name ?? undefined,
            message: null,
          })
        );
        return;
      }
      if (r.status === "partial" && r.candidates?.length) {
        setState((prev) =>
          fold(prev, {
            status: "partial",
            candidates: r.candidates,
            display_name: r.candidates![0].display_name,
            message: "Only the street was found. Confirm to use it anyway.",
          })
        );
        return;
      }
      if (r.status === "ambiguous" && r.candidates?.length) {
        setState((prev) =>
          fold(prev, {
            status: "ambiguous",
            candidates: r.candidates,
            message: "Several matches were found - pick one.",
          })
        );
        return;
      }
      if (r.status === "not_found") {
        setState((prev) => fold(prev, { status: "not_found", message: r.message || NOT_FOUND_MESSAGE }));
        return;
      }
      const setup = r.error_kind === "setup";
      setState((prev) =>
        fold(prev, { status: "error", message: setup ? SETUP_MESSAGE : RETRYABLE_MESSAGE })
      );
    } catch (e: any) {
      if (e.name === "AbortError") return;
      if (runId !== runIdRef.current) return;
      const setup = e.errorKind === "setup";
      setState((prev) =>
        fold(prev, { status: "error", message: setup ? SETUP_MESSAGE : RETRYABLE_MESSAGE })
      );
    }
  }

  function selectCandidate(index: number) {
    setState((prev) => {
      const c = prev.candidates?.[index];
      if (!c) return prev;
      return {
        ...prev,
        status: "resolved",
        coordinate: { latitude: c.latitude, longitude: c.longitude },
        display_name: c.display_name,
        message: null,
      };
    });
  }

  function confirmPartial() {
    setState((prev) => {
      const c = prev.candidates?.[0];
      if (!c) return prev;
      return {
        ...prev,
        status: "resolved",
        coordinate: { latitude: c.latitude, longitude: c.longitude },
        display_name: c.display_name,
        message: null,
      };
    });
  }

  return { state, setText, run, selectCandidate, confirmPartial };
}