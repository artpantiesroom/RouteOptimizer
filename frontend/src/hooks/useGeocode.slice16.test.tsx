import { renderHook, act, waitFor } from "@testing-library/react";
import { useGeocode } from "./useGeocode";
import * as api from "../api/client";
import type { ParseResponse } from "../api/client";

vi.mock("../api/client");

const mockedApi = vi.mocked(api);

/**
 * Fixtures are invented. Only the city names are real, because city scoping is
 * what these tests are about.
 */
function parseResponse(...originals: string[]): ParseResponse {
  return {
    items: originals.map((original, index) => ({
      index,
      original,
      trimmed: original,
      is_blank: false,
      is_duplicate: false,
      duplicate_of: null,
    })),
    total: originals.length,
    non_blank: originals.length,
    suggested_city: null,
  };
}

const KYIV_ROW = {
  index: 0,
  original: "вулиця Тестова, 1",
  status: "resolved",
  found_city: "Київ",
};
const POLTAVA_ROW = {
  index: 1,
  original: "вулиця Інша, 2",
  status: "not_found",
  found_city: "Полтава",
  needs_check: true,
  needs_check_reason: "Found in Полтава, outside Київ.",
  retry_city: "Київ",
};

describe("useGeocode - needs check", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("keeps the needs-check flags from the response", async () => {
    mockedApi.parse.mockResolvedValue(parseResponse(POLTAVA_ROW.original));
    mockedApi.geocodeBatch.mockResolvedValue({ results: [{ ...POLTAVA_ROW, index: 0 }] });

    const { result } = renderHook(() => useGeocode());
    await act(async () => {
      await result.current.run("x");
    });

    await waitFor(() => expect(result.current.state.items).toHaveLength(1));
    const row = result.current.state.items[0];
    expect(row.needs_check).toBe(true);
    expect(row.needs_check_reason).toBe("Found in Полтава, outside Київ.");
    expect(row.retry_city).toBe("Київ");
    expect(row.found_city).toBe("Полтава");
  });

  it("stores the city suggested by the resolved rows", async () => {
    mockedApi.parse.mockResolvedValue(parseResponse("a", "b", "c", "d", "e"));
    mockedApi.geocodeBatch.mockResolvedValue({
      results: [KYIV_ROW],
      city_suggestion: "Київ",
      city_suggestion_share: 0.8,
    });

    const { result } = renderHook(() => useGeocode());
    await act(async () => {
      await result.current.run("x");
    });

    await waitFor(() => expect(result.current.state.citySuggestion).toBe("Київ"));
    expect(result.current.state.citySuggestionShare).toBe(0.8);
    // A suggestion must never write itself into the City field.
    expect(result.current.state.city).toBe("");
  });

  it("re-runs only the flagged rows, scoped to the given city", async () => {
    mockedApi.parse.mockResolvedValue(parseResponse(KYIV_ROW.original, POLTAVA_ROW.original));
    mockedApi.geocodeBatch
      .mockResolvedValueOnce({ results: [KYIV_ROW, POLTAVA_ROW] })
      .mockResolvedValueOnce({
        results: [{ ...POLTAVA_ROW, status: "resolved", needs_check: false, found_city: "Київ" }],
      });

    const { result } = renderHook(() => useGeocode());
    await act(async () => {
      await result.current.run("x");
    });
    await waitFor(() => expect(result.current.state.items).toHaveLength(2));
    expect(mockedApi.geocodeBatch).toHaveBeenCalledTimes(1);

    await act(async () => {
      await result.current.retryFlaggedInCity("Київ");
    });

    await waitFor(() => expect(mockedApi.geocodeBatch).toHaveBeenCalledTimes(2));
    const [flaggedOnly, , city] = mockedApi.geocodeBatch.mock.calls[1];
    expect(flaggedOnly).toHaveLength(1);
    expect(flaggedOnly[0].index).toBe(POLTAVA_ROW.index);
    expect(city).toBe("Київ");
  });

  it("leaves the untouched row alone after a retry", async () => {
    mockedApi.parse.mockResolvedValue(parseResponse(KYIV_ROW.original, POLTAVA_ROW.original));
    mockedApi.geocodeBatch
      .mockResolvedValueOnce({ results: [KYIV_ROW, POLTAVA_ROW] })
      .mockResolvedValueOnce({
        results: [{ ...POLTAVA_ROW, status: "resolved", needs_check: false, found_city: "Київ" }],
      });

    const { result } = renderHook(() => useGeocode());
    await act(async () => {
      await result.current.run("x");
    });
    await waitFor(() => expect(result.current.state.items).toHaveLength(2));
    const before = result.current.state.items[0];

    await act(async () => {
      await result.current.retryFlaggedInCity("Київ");
    });

    await waitFor(() => expect(result.current.state.items[1].needs_check).toBe(false));
    expect(result.current.state.items[0]).toEqual(before);
  });

  it("does nothing when no row needs a check", async () => {
    mockedApi.parse.mockResolvedValue(parseResponse(KYIV_ROW.original));
    mockedApi.geocodeBatch.mockResolvedValue({ results: [KYIV_ROW] });

    const { result } = renderHook(() => useGeocode());
    await act(async () => {
      await result.current.run("x");
    });
    await waitFor(() => expect(result.current.state.items).toHaveLength(1));
    const calls = mockedApi.geocodeBatch.mock.calls.length;

    await act(async () => {
      await result.current.retryFlaggedInCity("Київ");
    });

    expect(mockedApi.geocodeBatch).toHaveBeenCalledTimes(calls);
  });

  it("does nothing for a blank city", async () => {
    mockedApi.parse.mockResolvedValue(parseResponse(POLTAVA_ROW.original));
    mockedApi.geocodeBatch.mockResolvedValue({ results: [POLTAVA_ROW] });

    const { result } = renderHook(() => useGeocode());
    await act(async () => {
      await result.current.run("x");
    });
    await waitFor(() => expect(result.current.state.items).toHaveLength(1));
    const calls = mockedApi.geocodeBatch.mock.calls.length;

    await act(async () => {
      await result.current.retryFlaggedInCity("   ");
    });

    expect(mockedApi.geocodeBatch).toHaveBeenCalledTimes(calls);
  });

  it("clears the suggestion when a new run starts", async () => {
    mockedApi.parse.mockResolvedValue(parseResponse("a", "b", "c", "d", "e"));
    mockedApi.geocodeBatch.mockResolvedValue({
      results: [KYIV_ROW],
      city_suggestion: "Київ",
    });

    const { result } = renderHook(() => useGeocode());
    await act(async () => {
      await result.current.run("x");
    });
    await waitFor(() => expect(result.current.state.citySuggestion).toBe("Київ"));

    mockedApi.geocodeBatch.mockResolvedValue({ results: [KYIV_ROW] });
    await act(async () => {
      await result.current.run("y");
    });
    await waitFor(() => expect(result.current.state.citySuggestion).toBe(null));
  });
});