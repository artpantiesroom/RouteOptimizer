import { renderHook, act, waitFor } from "@testing-library/react";
import { useGeocode } from "./useGeocode";
import * as api from "../api/client";
import type { GeocodeBatchResponse, ParseResponse } from "../api/client";

vi.mock("../api/client");

const mockedApi = vi.mocked(api);

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function abortError(): Error {
  const error = new Error("The operation was aborted.");
  error.name = "AbortError";
  return error;
}

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
  };
}

describe("useGeocode", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("parses and batches, accumulates results", async () => {
    mockedApi.parse.mockResolvedValue(parseResponse("A", "B"));

    mockedApi.geocodeBatch.mockResolvedValue({
      results: [
        { index: 0, original: "A", status: "resolved", display_name: "A ok" },
        { index: 1, original: "B", status: "not_found" },
      ],
    });

    const { result } = renderHook(() => useGeocode());
    await act(async () => {
      await result.current.run("A\nB");
    });

    await waitFor(() => expect(result.current.state.loading).toBe(false));
    expect(result.current.state.items[0].status).toBe("resolved");
    expect(result.current.state.items[1].status).toBe("not_found");
    expect(result.current.state.processed).toBe(2);
  });

  it("ignores a late success response from a previous run", async () => {
    // Run A has two addresses, so its single batch stays pending and, if it ever
    // leaked into state, would set processed to 2 instead of B's 1.
    mockedApi.parse
      .mockResolvedValueOnce(parseResponse("A1", "A2"))
      .mockResolvedValueOnce(parseResponse("B"));

    const runABatch = deferred<GeocodeBatchResponse>();
    mockedApi.geocodeBatch
      .mockImplementationOnce(() => runABatch.promise)
      .mockResolvedValueOnce({
        results: [{ index: 0, original: "B", status: "resolved", display_name: "B ok" }],
      });

    const { result } = renderHook(() => useGeocode());

    // Run A reaches geocodeBatch and stays pending there.
    await act(async () => {
      void result.current.run("A1\nA2");
    });
    expect(mockedApi.geocodeBatch).toHaveBeenCalledTimes(1);
    expect(result.current.state.loading).toBe(true);
    expect(result.current.state.items).toHaveLength(2);
    expect(result.current.state.processed).toBe(0);

    // Run B starts and runs to completion while A is still pending.
    await act(async () => {
      void result.current.run("B");
    });
    expect(mockedApi.geocodeBatch).toHaveBeenCalledTimes(2);
    expect(result.current.state.loading).toBe(false);
    expect(result.current.state.processed).toBe(1);

    // Only now does the stale response from run A arrive.
    await act(async () => {
      runABatch.resolve({
        results: [
          { index: 0, original: "A1", status: "error", error_message: "stale failure" },
          { index: 1, original: "A2", status: "error", error_message: "stale failure" },
        ],
      });
      await runABatch.promise;
    });

    // State must still describe run B only.
    expect(result.current.state.items).toHaveLength(1);
    expect(result.current.state.items[0].original).toBe("B");
    expect(result.current.state.items[0].status).toBe("resolved");
    expect(result.current.state.items.some((i) => i.status === "error")).toBe(false);
    expect(result.current.state.total).toBe(1);
    expect(result.current.state.processed).toBe(1);
    expect(result.current.state.loading).toBe(false);
    expect(result.current.state.error).toBeNull();
  });

  it("ignores an abort rejection from a previous run", async () => {
    mockedApi.parse
      .mockResolvedValueOnce(parseResponse("A1", "A2"))
      .mockResolvedValueOnce(parseResponse("B"));

    const runABatch = deferred<GeocodeBatchResponse>();
    mockedApi.geocodeBatch
      .mockImplementationOnce(() => runABatch.promise)
      .mockResolvedValueOnce({
        results: [{ index: 0, original: "B", status: "resolved", display_name: "B ok" }],
      });

    const { result } = renderHook(() => useGeocode());

    await act(async () => {
      void result.current.run("A1\nA2");
    });
    expect(mockedApi.geocodeBatch).toHaveBeenCalledTimes(1);

    await act(async () => {
      void result.current.run("B");
    });
    expect(mockedApi.geocodeBatch).toHaveBeenCalledTimes(2);
    expect(result.current.state.loading).toBe(false);

    // The aborted first run rejects after run B already finished.
    await act(async () => {
      runABatch.reject(abortError());
      await runABatch.promise.catch(() => undefined);
    });

    expect(result.current.state.items).toHaveLength(1);
    expect(result.current.state.items[0].original).toBe("B");
    expect(result.current.state.items[0].status).toBe("resolved");
    expect(result.current.state.items.some((i) => i.status === "error")).toBe(false);
    expect(result.current.state.processed).toBe(1);
    expect(result.current.state.loading).toBe(false);
    // An AbortError must never surface as a user-visible error.
    expect(result.current.state.error).toBeNull();
  });

  it("retries a single retryable row without touching the others", async () => {
    mockedApi.parse.mockResolvedValue(parseResponse("A", "B"));

    mockedApi.geocodeBatch
      .mockResolvedValueOnce({
        results: [
          {
            index: 0,
            original: "A",
            status: "error",
            error_kind: "retryable",
            error_message: "request timed out",
          },
          { index: 1, original: "B", status: "resolved", display_name: "B ok" },
        ],
      })
      .mockResolvedValueOnce({
        results: [
          {
            index: 0,
            original: "A",
            status: "resolved",
            display_name: "A ok",
          },
        ],
      });

    const { result } = renderHook(() => useGeocode());
    await act(async () => {
      await result.current.run("A\nB");
    });

    expect(result.current.state.items[0].status).toBe("error");
    expect(result.current.state.items[0].error_kind).toBe("retryable");

    await act(async () => {
      await result.current.retryItem(0);
    });

    // Only row 0 changed; row 1 is untouched.
    expect(result.current.state.items[0].status).toBe("resolved");
    expect(result.current.state.items[0].retrying).toBe(false);
    expect(result.current.state.items[1].status).toBe("resolved");
    expect(result.current.state.items[1].retrying).toBeUndefined();
    // The retry must not be counted as new batch progress.
    expect(result.current.state.processed).toBe(2);
  });

  it("does not retry a setup error", async () => {
    mockedApi.parse.mockResolvedValue(parseResponse("A"));
    mockedApi.geocodeBatch.mockResolvedValue({
      results: [
        {
          index: 0,
          original: "A",
          status: "error",
          error_kind: "setup",
          error_message: "service rejected request (status 403)",
        },
      ],
    });

    const { result } = renderHook(() => useGeocode());
    await act(async () => {
      await result.current.run("A");
    });

    expect(result.current.state.items[0].error_kind).toBe("setup");

    await act(async () => {
      await result.current.retryItem(0);
    });

    // No extra network call: a setup error will not change on retry.
    expect(mockedApi.geocodeBatch).toHaveBeenCalledTimes(1);
    expect(result.current.state.items[0].status).toBe("error");
    expect(result.current.state.items[0].error_kind).toBe("setup");
  });

  it("keeps a row retryable when the retry itself fails", async () => {
    mockedApi.parse.mockResolvedValue(parseResponse("A"));
    mockedApi.geocodeBatch
      .mockResolvedValueOnce({
        results: [
          {
            index: 0,
            original: "A",
            status: "error",
            error_kind: "retryable",
            error_message: "request timed out",
          },
        ],
      })
      .mockRejectedValueOnce(new Error("network down"));

    const { result } = renderHook(() => useGeocode());
    await act(async () => {
      await result.current.run("A");
    });

    await act(async () => {
      await result.current.retryItem(0);
    });

    expect(result.current.state.items[0].status).toBe("error");
    expect(result.current.state.items[0].error_kind).toBe("retryable");
    expect(result.current.state.items[0].retrying).toBe(false);
  });

  it("skips a row and brings it back with toggleSkip", async () => {
    mockedApi.parse.mockResolvedValue(parseResponse("A", "B"));
    mockedApi.geocodeBatch.mockResolvedValue({
      results: [
        { index: 0, original: "A", status: "resolved", display_name: "A ok" },
        { index: 1, original: "B", status: "resolved", display_name: "B ok" },
      ],
    });

    const { result } = renderHook(() => useGeocode());
    await act(async () => {
      await result.current.run("A\nB");
    });
    await waitFor(() => expect(result.current.state.loading).toBe(false));

    act(() => result.current.toggleSkip(0));
    expect(result.current.state.items[0].skipped).toBe(true);
    expect(result.current.state.items[1].skipped).toBe(false);

    act(() => result.current.toggleSkip(0));
    expect(result.current.state.items[0].skipped).toBe(false);
  });

  it("a fresh answer brings a skipped row back into play", async () => {
    mockedApi.parse.mockResolvedValue(parseResponse("A"));
    mockedApi.geocodeBatch.mockResolvedValue({
      results: [{ index: 0, original: "A", status: "resolved", display_name: "A ok" }],
    });

    const { result } = renderHook(() => useGeocode());
    await act(async () => {
      await result.current.run("A");
    });
    await waitFor(() => expect(result.current.state.loading).toBe(false));

    act(() => result.current.toggleSkip(0));
    expect(result.current.state.items[0].skipped).toBe(true);

    // Editing the row re-checks it, and the fresh answer returns it to the route.
    await act(async () => {
      await result.current.editItem(0, "вулиця Нова, 2");
    });
    expect(result.current.state.items[0].skipped).toBe(false);
    expect(result.current.state.items[0].rechecking).toBe(false);
  });
});