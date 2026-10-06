import { renderHook, act, waitFor } from "@testing-library/react";
import { useMatrix } from "./useMatrix";
import * as api from "../api/client";
import type { MatrixResponse } from "../api/client";

vi.mock("../api/client");

const mockedApi = vi.mocked(api);

const POINTS = [
  { id: "start", lat: 50.45, lon: 30.52 },
  { id: "0", lat: 50.44, lon: 30.51 },
];

function okResponse(): MatrixResponse {
  return {
    ids: ["start", "0"],
    durations_s: [
      [0, 604],
      [610, 0],
    ],
    distances_m: [
      [0, 5200],
      [5300, 0],
    ],
    problems: [],
    provider: "osrm",
    profile: "driving",
    note: "Durations are typical driving times without live traffic.",
  };
}

describe("useMatrix", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("posts the points and stores the result", async () => {
    mockedApi.matrix.mockResolvedValue(okResponse());
    const { result } = renderHook(() => useMatrix());

    await act(async () => {
      await result.current.calculate(POINTS);
    });

    expect(mockedApi.matrix).toHaveBeenCalledWith(POINTS);
    await waitFor(() => expect(result.current.state.loading).toBe(false));
    expect(result.current.state.result?.ids).toEqual(["start", "0"]);
    expect(result.current.state.error).toBeNull();
  });

  it("maps a retryable failure to the retry wording", async () => {
    mockedApi.matrix.mockRejectedValue({ name: "ApiError", status: 502, errorKind: "retryable" });
    const { result } = renderHook(() => useMatrix());

    await act(async () => {
      await result.current.calculate(POINTS);
    });

    expect(result.current.state.error).toBe("Could not calculate the route right now. Try again.");
    expect(result.current.state.result).toBeNull();
  });

  it("maps a setup failure to the setup wording", async () => {
    mockedApi.matrix.mockRejectedValue({ name: "ApiError", status: 502, errorKind: "setup" });
    const { result } = renderHook(() => useMatrix());

    await act(async () => {
      await result.current.calculate(POINTS);
    });

    expect(result.current.state.error).toBe(
      "The route service rejected the request. This is a setup problem, not your addresses."
    );
  });

  it("shows a 400 detail as-is", async () => {
    mockedApi.matrix.mockRejectedValue({ name: "ApiError", status: 400, errorKind: null, message: "Too many points. Reduce the list." });
    const { result } = renderHook(() => useMatrix());

    await act(async () => {
      await result.current.calculate(POINTS);
    });

    expect(result.current.state.error).toBe("Too many points. Reduce the list.");
  });

  it("sends a fresh request on the next press and replaces the old result", async () => {
    mockedApi.matrix.mockResolvedValueOnce(okResponse());
    const { result } = renderHook(() => useMatrix());

    await act(async () => {
      await result.current.calculate(POINTS);
    });
    expect(result.current.state.result).not.toBeNull();

    mockedApi.matrix.mockResolvedValueOnce({ ...okResponse(), ids: ["start"] });
    await act(async () => {
      await result.current.calculate([POINTS[0]]);
    });

    expect(result.current.state.result?.ids).toEqual(["start"]);
  });
});