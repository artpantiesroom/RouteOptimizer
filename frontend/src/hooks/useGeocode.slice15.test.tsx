import { renderHook, act, waitFor } from "@testing-library/react";
import { useGeocode } from "./useGeocode";
import * as api from "../api/client";
import type { ParseResponse } from "../api/client";

vi.mock("../api/client");

const mockedApi = vi.mocked(api);

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

function parseResponseWithCity(city: string | null, ...originals: string[]): ParseResponse {
  return { ...parseResponse(...originals), suggested_city: city };
}

describe("useGeocode - City field", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("prefills the city from the first address", async () => {
    mockedApi.parse.mockResolvedValue(parseResponseWithCity("Київ", "ул. Покровская, 8, Киев"));
    mockedApi.geocodeBatch.mockResolvedValue({
      results: [{ index: 0, original: "x", status: "resolved" }],
    });

    const { result } = renderHook(() => useGeocode());
    await act(async () => {
      await result.current.run("ул. Покровская, 8, Киев");
    });

    await waitFor(() => expect(result.current.state.city).toBe("Київ"));
  });

  it("sends the prefilled city with the geocode request", async () => {
    mockedApi.parse.mockResolvedValue(parseResponseWithCity("Київ", "A"));
    mockedApi.geocodeBatch.mockResolvedValue({ results: [{ index: 0, original: "A", status: "resolved" }] });

    const { result } = renderHook(() => useGeocode());
    await act(async () => {
      await result.current.run("A");
    });

    await waitFor(() => expect(mockedApi.geocodeBatch).toHaveBeenCalled());
    expect(mockedApi.geocodeBatch.mock.calls[0][2]).toBe("Київ");
  });

  it("keeps a city the user typed instead of overwriting it", async () => {
    mockedApi.parse.mockResolvedValue(parseResponseWithCity("Київ", "A"));
    mockedApi.geocodeBatch.mockResolvedValue({ results: [{ index: 0, original: "A", status: "resolved" }] });

    const { result } = renderHook(() => useGeocode());
    act(() => result.current.setCity("Львів"));
    await act(async () => {
      await result.current.run("A");
    });

    await waitFor(() => expect(result.current.state.city).toBe("Львів"));
    expect(mockedApi.geocodeBatch.mock.calls[0][2]).toBe("Львів");
  });

  it("leaves the city blank when no address names a known city", async () => {
    mockedApi.parse.mockResolvedValue(parseResponseWithCity(null, "A"));
    mockedApi.geocodeBatch.mockResolvedValue({ results: [{ index: 0, original: "A", status: "not_found" }] });

    const { result } = renderHook(() => useGeocode());
    await act(async () => {
      await result.current.run("A");
    });

    await waitFor(() => expect(result.current.state.loading).toBe(false));
    expect(result.current.state.city).toBe("");
  });

  it("keeps the city across a second run", async () => {
    mockedApi.parse.mockResolvedValue(parseResponseWithCity("Київ", "A"));
    mockedApi.geocodeBatch.mockResolvedValue({ results: [{ index: 0, original: "A", status: "resolved" }] });

    const { result } = renderHook(() => useGeocode());
    await act(async () => {
      await result.current.run("A");
    });
    await waitFor(() => expect(result.current.state.city).toBe("Київ"));

    mockedApi.geocodeBatch.mockResolvedValue({ results: [{ index: 0, original: "A", status: "not_found" }] });
    await act(async () => {
      await result.current.run("A");
    });

    expect(result.current.state.city).toBe("Київ");
  });
});

describe("useGeocode - normalization details in the result", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("stores the searched query, unit reading and dropped count", async () => {
    mockedApi.parse.mockResolvedValue(parseResponse("ул. Буд, 15-9, Киев"));
    mockedApi.geocodeBatch.mockResolvedValue({
      results: [
        {
          index: 0,
          original: "ул. Буд, 15-9, Киев",
          status: "resolved",
          searched_as: "вулиця Буд, 15-9, Київ",
          house: "15",
          unit: "9",
          unit_kind: "apartment",
          unit_inferred: true,
          dropped_candidates: 3,
          scope_message: "3 matches were ignored (outside Київ).",
        },
      ],
    });

    const { result } = renderHook(() => useGeocode());
    await act(async () => {
      await result.current.run("ул. Буд, 15-9, Киев");
    });

    await waitFor(() => expect(result.current.state.loading).toBe(false));
    const item = result.current.state.items[0];
    expect(item.searched_as).toBe("вулиця Буд, 15-9, Київ");
    expect(item.house).toBe("15");
    expect(item.unit).toBe("9");
    expect(item.unit_kind).toBe("apartment");
    expect(item.unit_inferred).toBe(true);
    expect(item.dropped_candidates).toBe(3);
    expect(item.scope_message).toBe("3 matches were ignored (outside Київ).");
  });
});

describe("useGeocode - editing a row", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  async function runOnce(address: string) {
    mockedApi.parse.mockResolvedValue(parseResponse(address));
    mockedApi.geocodeBatch.mockResolvedValue({
      results: [{ index: 0, original: address, status: "not_found" }],
    });
    const { result } = renderHook(() => useGeocode());
    await act(async () => {
      await result.current.run(address);
    });
    await waitFor(() => expect(result.current.state.loading).toBe(false));
    return result;
  }

  it("re-runs only the edited row with the new text", async () => {
    const result = await runOnce("Wrong Street 1");
    mockedApi.geocodeBatch.mockClear();

    mockedApi.geocodeBatch.mockResolvedValue({
      results: [
        {
          index: 0,
          original: "Right Street 2",
          status: "resolved",
          display_name: "Right ok",
          searched_as: "Right Street 2",
        },
      ],
    });

    await act(async () => {
      await result.current.editItem(0, "  Right Street 2  ");
    });

    expect(mockedApi.geocodeBatch).toHaveBeenCalledTimes(1);
    const [items, , city] = mockedApi.geocodeBatch.mock.calls[0];
    expect(items).toEqual([{ index: 0, original: "Right Street 2", trimmed: "Right Street 2" }]);
    expect(city).toBe("");
    expect(result.current.state.items[0].status).toBe("resolved");
    expect(result.current.state.items[0].original).toBe("Right Street 2");
  });

  it("clears the previous answer when the row is re-run", async () => {
    mockedApi.parse.mockResolvedValue(parseResponse("A"));
    mockedApi.geocodeBatch.mockResolvedValue({
      results: [
        {
          index: 0,
          original: "A",
          status: "ambiguous",
          candidates: [
            { display_name: "one", latitude: 1, longitude: 1 },
            { display_name: "two", latitude: 2, longitude: 2 },
          ],
        },
      ],
    });
    const { result } = renderHook(() => useGeocode());
    await act(async () => {
      await result.current.run("A");
    });
    await waitFor(() => expect(result.current.state.items[0].status).toBe("ambiguous"));

    act(() => result.current.selectCandidate(0, 0));
    act(() => result.current.confirmPartial(0));
    expect(result.current.state.items[0].selectedCandidateIndex).toBe(0);
    expect(result.current.state.items[0].confirmed).toBe(true);

    // Re-running the row returns a plain answer with no candidate list.
    mockedApi.geocodeBatch.mockResolvedValue({
      results: [
        { index: 0, original: "B", status: "resolved", display_name: "B ok", searched_as: "B" },
      ],
    });
    await act(async () => {
      await result.current.editItem(0, "B");
    });

    const item = result.current.state.items[0];
    expect(item.status).toBe("resolved");
    expect(item.selectedCandidateIndex).toBeUndefined();
    expect(item.confirmed).toBeUndefined();
    expect(item.candidates).toBeUndefined();
    expect(item.offline).toBe(false);
  });

  it("ignores an edit to blank text", async () => {
    const result = await runOnce("A");
    mockedApi.geocodeBatch.mockClear();

    await act(async () => {
      await result.current.editItem(0, "   ");
    });

    expect(mockedApi.geocodeBatch).not.toHaveBeenCalled();
    expect(result.current.state.items[0].original).toBe("A");
  });

  it("keeps the edit when the service cannot be reached", async () => {
    const result = await runOnce("A");
    mockedApi.geocodeBatch.mockRejectedValue(new Error("network down"));

    await act(async () => {
      await result.current.editItem(0, "Edited");
    });

    const item = result.current.state.items[0];
    expect(item.original).toBe("Edited");
    expect(item.trimmed).toBe("Edited");
    expect(item.status).toBe("error");
    expect(item.error_kind).toBe("retryable");
    expect(item.offline).toBe(true);
    expect(item.rechecking).toBe(false);
  });

  it("passes the current city when re-running a row", async () => {
    const result = await runOnce("A");
    act(() => result.current.setCity("Львів"));
    mockedApi.geocodeBatch.mockClear();

    await act(async () => {
      await result.current.editItem(0, "B");
    });

    expect(mockedApi.geocodeBatch.mock.calls[0][2]).toBe("Львів");
  });
});