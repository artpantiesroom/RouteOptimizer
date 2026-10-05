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
}

export interface GeocodeItem {
  index: number;
  original: string;
  trimmed: string;
}

export interface GeocodeBatchRequest {
  items: GeocodeItem[];
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
}

export interface GeocodeBatchResponse {
  results: GeocodeBatchResponseItem[];
}

export async function parse(text: string): Promise<ParseResponse> {
  const res = await fetch(`${API_BASE}/parse`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) {
    throw new Error(await res.text());
  }
  return res.json();
}

export async function geocodeBatch(
  items: GeocodeItem[],
  signal?: AbortSignal
): Promise<GeocodeBatchResponse> {
  if (items.length > 5) {
    throw new Error("Batch size cannot exceed 5");
  }
  const res = await fetch(`${API_BASE}/geocode`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
    signal,
  });
  if (!res.ok) {
    throw new Error(await res.text());
  }
  return res.json();
}
