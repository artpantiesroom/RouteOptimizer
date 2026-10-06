/**
 * Decides whether the "Calculate route" button may be pressed and which rows
 * go into the route. Pure over row states so it is trivially testable.
 *
 * A row is usable only when it has proven where it is, or the user said so:
 * resolved and not flagged as needing a check, OR explicitly confirmed (a
 * partial match), OR explicitly set aside (skipped). Everything else - a row
 * that needs a check, an ambiguous pick without a confirmed choice, an
 * unconfirmed partial match, a not-found or failed row - blocks the button and
 * never reaches the route request. Blank rows are simply absent.
 *
 * Skipped rows are allowed because the user chose them out; a needs_check row
 * is not usable until it is confirmed, fixed, or skipped.
 */

export interface RowReadiness {
  is_blank: boolean;
  status: string;
  confirmed?: boolean;
  needs_check?: boolean;
  skipped?: boolean;
}

/** True when a row may take part in the route request right now. */
export function isUsableStop(row: RowReadiness): boolean {
  if (row.is_blank) return false;
  if (row.skipped === true) return false;
  if (row.status !== "resolved" && row.confirmed !== true) return false;
  if (row.needs_check === true && row.confirmed !== true) return false;
  return true;
}

/**
 * True when the row does not block the button: it either goes into the request
 * as-is (resolved-ok or user-confirmed) or the user explicitly set it aside.
 * A skipped row is accepted by the list but never sent to the routing service.
 */
export function isRowAccepted(row: RowReadiness): boolean {
  if (row.is_blank) return false;
  if (row.skipped === true) return true;
  return isUsableStop(row);
}

/** The rows that may be sent to the routing service, in list order. */
export function usableStopRows<T extends RowReadiness>(rows: T[]): T[] {
  return rows.filter(isUsableStop);
}

export interface CanCalculateParams {
  /** The start point input has produced a usable coordinate. */
  startResolved: boolean;
  rows: RowReadiness[];
}

export type CanCalculateResult = { ok: true; reason: null } | { ok: false; reason: string };

export function canCalculateRoute({ startResolved, rows }: CanCalculateParams): CanCalculateResult {
  const active = rows.filter((r) => !r.is_blank);

  if (!startResolved) {
    return { ok: false, reason: "Resolve the start point first." };
  }
  if (active.length === 0) {
    return { ok: false, reason: "Add at least one address to plan around." };
  }
  if (!active.every(isRowAccepted)) {
    return { ok: false, reason: "Resolve or skip every address before calculating the route." };
  }
  if (active.filter(isUsableStop).length === 0) {
    return { ok: false, reason: "The route needs at least one address that is not skipped." };
  }
  return { ok: true, reason: null };
}