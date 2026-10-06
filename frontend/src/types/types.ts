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
  /** The exact text that was sent to the address search service. */
  searched_as?: string | null;
  /** House number, needed to explain an inferred unit. */
  house?: string | null;
  /** Apartment / office / entrance / floor taken out of the address. */
  unit?: string | null;
  unit_kind?: UnitKind | null;
  /** True when the unit was read from an ambiguous "15-9" form. */
  unit_inferred?: boolean;
  /** Matches that were discarded because they were outside the chosen city. */
  dropped_candidates?: number;
  /** Plain-language note about the discarded matches. */
  scope_message?: string | null;
  /** A per-row re-check is in flight (after an in-place edit). */
  rechecking?: boolean;
  /** Set when the address search service could not be reached for this row. */
  offline?: boolean;
  /** The house number that was actually found, when it differs from the request. */
  found_house?: string | null;
  /** The city the chosen coordinate sits in. */
  found_city?: string | null;
  /**
   * Set when the row is consistent on its own but suspicious in the context of
   * the list: outside the active city, or far from every other stop. A row like
   * this is not counted as resolved.
   */
  needs_check?: boolean;
  /** Why the row needs a check, in plain language. */
  needs_check_reason?: string | null;
  /** City to re-run this row in, when one is known. */
  retry_city?: string | null;
  /**
   * The user chose to set this row aside: it stays visible but takes no part
   * in the route. Nothing is dropped silently.
   */
  skipped?: boolean;
}

/** What a house unit refers to, as understood from the address text. */
export type UnitKind = "apartment" | "office" | "entrance" | "floor";

/** Plain-language label for a unit, used in the interpretation line. */
export const UNIT_KIND_LABELS: Record<UnitKind, string> = {
  apartment: "Apartment",
  office: "Office",
  entrance: "Entrance",
  floor: "Floor",
};

export interface ParsedItem {
  index: number;
  original: string;
  trimmed: string;
  is_blank: boolean;
  is_duplicate: boolean;
  duplicate_of: number | null;
}
