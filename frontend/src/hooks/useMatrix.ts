import { useState, useRef } from "react";
import { matrix, type MatrixPoint, type MatrixResponse } from "../api/client";

/**
 * Runs POST /api/matrix for a fixed list of points. The caller stays in
 * charge of the point list; this hook only turns presses into results.
 */

const RETRYABLE_MESSAGE = "Could not calculate the route right now. Try again.";
const SETUP_MESSAGE =
  "The route service rejected the request. This is a setup problem, not your addresses.";

export interface UseMatrixState {
  result: MatrixResponse | null;
  loading: boolean;
  error: string | null;
}

export function useMatrix() {
  const [state, setState] = useState<UseMatrixState>({
    result: null,
    loading: false,
    error: null,
  });
  const runIdRef = useRef(0);

  async function calculate(points: MatrixPoint[]): Promise<void> {
    const runId = ++runIdRef.current;
    setState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const result = await matrix(points);
      if (runId !== runIdRef.current) return;
      setState({ result, loading: false, error: null });
    } catch (e: any) {
      if (e.name === "AbortError") return;
      if (runId !== runIdRef.current) return;
      const kind = e && typeof e === "object" ? e.errorKind : undefined;
      const status = e && typeof e === "object" ? e.status : undefined;
      const message =
        kind === "setup"
          ? SETUP_MESSAGE
          : status === 400
            ? e.message
            : RETRYABLE_MESSAGE;
      setState((prev) => ({ ...prev, loading: false, error: message }));
    }
  }

  return { state, calculate };
}