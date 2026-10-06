import {
  canCalculateRoute,
  isUsableStop,
  usableStopRows,
  type RowReadiness,
} from "./routeReady";

function row(overrides: Partial<RowReadiness> = {}): RowReadiness {
  return { is_blank: false, status: "resolved", ...overrides };
}

describe("canCalculateRoute", () => {
  it("is ok with a resolved start and resolved rows", () => {
    expect(canCalculateRoute({ startResolved: true, rows: [row()] })).toEqual({
      ok: true,
      reason: null,
    });
  });

  it("refuses when the start is not resolved", () => {
    const result = canCalculateRoute({ startResolved: false, rows: [row()] });
    expect(result.ok).toBe(false);
  });

  it("refuses when there are no non-blank rows", () => {
    const result = canCalculateRoute({ startResolved: true, rows: [row({ is_blank: true })] });
    expect(result.ok).toBe(false);
  });

  it("skips blank rows but still requires one usable address", () => {
    const result = canCalculateRoute({
      startResolved: true,
      rows: [row({ is_blank: true }), row()],
    });
    expect(result.ok).toBe(true);
  });

  it("allows an explicitly confirmed partial row", () => {
    const result = canCalculateRoute({
      startResolved: true,
      rows: [row({ status: "partial", confirmed: true })],
    });
    expect(result.ok).toBe(true);
  });

  it("refuses when a non-blank row is unresolved", () => {
    for (const status of ["partial", "ambiguous", "not_found", "error"]) {
      const result = canCalculateRoute({ startResolved: true, rows: [row({ status })] });
      expect(result.ok).toBe(false);
    }
  });

  it("refuses when any one of several rows is unresolved", () => {
    const result = canCalculateRoute({
      startResolved: true,
      rows: [row(), row({ status: "not_found" })],
    });
    expect(result.ok).toBe(false);
  });

  it("blocks the button on a resolved row that needs a check", () => {
    const result = canCalculateRoute({
      startResolved: true,
      rows: [row({ status: "resolved", needs_check: true })],
    });
    expect(result.ok).toBe(false);
  });

  it("is ok once the needs-check row is confirmed", () => {
    const result = canCalculateRoute({
      startResolved: true,
      rows: [row({ status: "resolved", needs_check: true, confirmed: true })],
    });
    expect(result.ok).toBe(true);
  });

  it("is ok once the needs-check row is skipped", () => {
    const result = canCalculateRoute({
      startResolved: true,
      rows: [row({ status: "resolved", needs_check: true, skipped: true }), row()],
    });
    expect(result.ok).toBe(true);
  });

  it("refuses when every non-blank row is skipped", () => {
    const result = canCalculateRoute({
      startResolved: true,
      rows: [row({ status: "resolved", skipped: true }), row({ status: "partial", skipped: true })],
    });
    expect(result.ok).toBe(false);
  });

  it("allows a skipped row to sit next to ready ones", () => {
    const result = canCalculateRoute({
      startResolved: true,
      rows: [row(), row({ status: "not_found", skipped: true })],
    });
    expect(result.ok).toBe(true);
  });

  it("refuses when every non-blank row is blocked", () => {
    const result = canCalculateRoute({
      startResolved: true,
      rows: [row({ status: "error" }), row({ needs_check: true })],
    });
    expect(result.ok).toBe(false);
  });
});

describe("usableStopRows", () => {
  it("keeps resolved-ok and confirmed rows in order", () => {
    const rows = [
      row({ status: "resolved" }),
      row({ status: "partial", confirmed: true }),
      row({ is_blank: true }),
    ];
    expect(usableStopRows(rows).length).toBe(2);
  });

  it("never returns a needs_check, ambiguous, partial-unconfirmed, not_found, error or skipped row", () => {
    const rows = [
      row({ status: "resolved", needs_check: true }),
      row({ status: "ambiguous" }),
      row({ status: "partial" }),
      row({ status: "not_found" }),
      row({ status: "error" }),
      row({ status: "resolved", skipped: true }),
      row({ is_blank: true }),
    ];
    expect(usableStopRows(rows)).toEqual([]);
  });

  it("drops a skipped row even when it is otherwise resolved", () => {
    const rows = [row({ status: "resolved", skipped: true }), row()];
    expect(usableStopRows(rows).length).toBe(1);
  });
});

describe("isUsableStop", () => {
  it("is false for blank, skipped, needs-check and unresolved rows", () => {
    expect(isUsableStop(row({ is_blank: true }))).toBe(false);
    expect(isUsableStop(row({ skipped: true }))).toBe(false);
    expect(isUsableStop(row({ status: "resolved", needs_check: true }))).toBe(false);
    expect(isUsableStop(row({ status: "partial" }))).toBe(false);
    expect(isUsableStop(row({ status: "ambiguous" }))).toBe(false);
    expect(isUsableStop(row({ status: "not_found" }))).toBe(false);
    expect(isUsableStop(row({ status: "error" }))).toBe(false);
  });

  it("is true for resolved-ok, confirmed, and needs-check-confirmed rows", () => {
    expect(isUsableStop(row({ status: "resolved" }))).toBe(true);
    expect(isUsableStop(row({ status: "partial", confirmed: true }))).toBe(true);
    expect(isUsableStop(row({ status: "resolved", needs_check: true, confirmed: true }))).toBe(true);
  });
});