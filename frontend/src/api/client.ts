const API_BASE = import.meta.env.VITE_API_BASE || "/api";

export interface ParseRequest {
  text: string;
  max_lines?: number;
  max_line_length?: number;
}

export interface ParsedItem {
  index: number;
  original: string;
  trimmed: string;
  is_blank: boolean;
  is_duplicate: boolean;
  duplicate_of: number | null;
}

export interface ParseResponse {
  items: ParsedItem[];
  total: number;
  non_blank: number;
  /** City recognised in the first address, used to prefill the City field. */
  suggested_city?: string | null;
}

export interface GeocodeItem {
  index: number;
  original: string;
  trimmed: string;
}

export interface GeocodeBatchRequest {
  items: GeocodeItem[];
  /** Scope for candidate validation; omit to disable city filtering. */
  city?: string | null;
}

export interface GeocodeCandidate {
  display_name: string;
  latitude: number;
  longitude: number;
  address?: Record<string, unknown>;
}

export interface GeocodeBatchResponseItem {
  index: number;
  original: string;
  status: string;
  coordinate?: { latitude: number; longitude: number };
  display_name?: string;
  candidates?: GeocodeCandidate[];
  error_message?: string;
  message?: string;
  error_kind?: string;
  searched_as?: string;
  house?: string | null;
  unit?: string | null;
  unit_kind?: string | null;
  unit_inferred?: boolean;
  dropped_candidates?: number;
  scope_message?: string | null;
  found_house?: string | null;
  found_city?: string | null;
  needs_check?: boolean;
  needs_check_reason?: string | null;
  retry_city?: string | null;
}

export interface GeocodeBatchResponse {
  results: GeocodeBatchResponseItem[];
  /** Offered for the City field when it was left blank. Never applied silently. */
  city_suggestion?: string | null;
  city_suggestion_share?: number | null;
}

/** One row of the distance matrix; the first point is the start point. */
export interface MatrixPoint {
  id: string;
  lat: number;
  lon: number;
}

export interface MatrixProblem {
  id: string;
  kind: string;
  message: string;
}

export interface MatrixResponse {
  /** Point ids in matrix order; the start point is first. */
  ids: string[];
  /** Rounded to whole seconds / metres; null means no route between that pair. */
  durations_s: (number | null)[][];
  distances_m: (number | null)[][];
  problems: MatrixProblem[];
  provider: string;
  profile: string;
  note: string;
}

/** An API error with enough structure to pick the user-facing wording. */
export class ApiError extends Error {
  status: number;
  errorKind: "retryable" | "setup" | null;

  constructor(message: string, status: number, errorKind: "retryable" | "setup" | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.errorKind = errorKind;
  }
}

function apiErrorFrom(res: Response, text: string): ApiError {
  let message = text;
  let errorKind: "retryable" | "setup" | null = null;
  try {
    const data = JSON.parse(text);
    if (typeof data.detail === "string") {
      message = data.detail;
    } else if (data.detail && typeof data.detail.message === "string") {
      message = data.detail.message;
      errorKind = data.detail.error_kind ?? null;
    }
  } catch {
    // keep the raw text
  }
  return new ApiError(message, res.status, errorKind);
}

export async function parse(text: string): Promise<ParseResponse> {
  const res = await fetch(`${API_BASE}/parse`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) {
    throw apiErrorFrom(res, await res.text());
  }
  return res.json();
}

export async function geocodeBatch(
  items: GeocodeItem[],
  signal?: AbortSignal,
  city?: string | null
): Promise<GeocodeBatchResponse> {
  if (items.length > 5) {
    throw new Error("Batch size cannot exceed 5");
  }
  const res = await fetch(`${API_BASE}/geocode`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items, city: city || null }),
    signal,
  });
  if (!res.ok) {
    throw apiErrorFrom(res, await res.text());
  }
  return res.json();
}

export async function matrix(
  points: MatrixPoint[],
  signal?: AbortSignal
): Promise<MatrixResponse> {
  const res = await fetch(`${API_BASE}/matrix`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(points),
    signal,
  });
  if (!res.ok) {
    throw apiErrorFrom(res, await res.text());
  }
  return res.json();
}
