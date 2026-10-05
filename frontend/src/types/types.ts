export type GeocodeStatus =
  | "resolved"
  | "partial"
  | "ambiguous"
  | "not_found"
  | "error";

/**
 * How an error should be presented:
 * - retryable: temporary condition, retrying the same address can succeed.
 * - setup: the address search service rejected the request itself, so the
 *   address is not at fault and retrying will not help.
 */
export type ErrorKind = "retryable" | "setup";

export interface Coordinate {
  latitude: number;
  longitude: number;
}

export interface GeocodeCandidate {
  display_name: string;
  latitude: number;
  longitude: number;
  address?: Record<string, unknown>;
}

export interface GeocodeResultItem {
  id: number; // row index from parse
  original: string;
  trimmed: string;
  is_blank: boolean;
  is_duplicate: boolean;
  duplicate_of: number | null;
  status: GeocodeStatus;
  coordinate?: Coordinate;
  display_name?: string;
  candidates?: GeocodeCandidate[];
  error_message?: string | null;
  message?: string | null;
  error_kind?: ErrorKind;
  selectedCandidateIndex?: number;
  confirmed?: boolean; // for partial after explicit confirmation
  retrying?: boolean; // a per-row retry is in flight
}

export interface ParsedItem {
  index: number;
  original: string;
  trimmed: string;
  is_blank: boolean;
  is_duplicate: boolean;
  duplicate_of: number | null;
}
