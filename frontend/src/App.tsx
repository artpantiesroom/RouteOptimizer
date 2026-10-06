import { useMemo, useState } from "react";
import { useGeocode } from "./hooks/useGeocode";
import { useMatrix } from "./hooks/useMatrix";
import { useStartPoint } from "./hooks/useStartPoint";
import { AddressInput } from "./components/AddressInput";
import { ResultsList } from "./components/ResultsList";
import { StartPointInput } from "./components/StartPointInput";
import { canCalculateRoute, usableStopRows } from "./lib/routeReady";
import type { MatrixPoint } from "./api/client";

function minutes(seconds: number | null): string {
  return seconds === null ? "--" : (seconds / 60).toFixed(1);
}

function kilometers(meters: number | null): string {
  return meters === null ? "--" : (meters / 1000).toFixed(1);
}

function MatrixTable({
  rows,
  cells,
  format,
}: {
  rows: string[];
  cells: (number | null)[][];
  format: (v: number | null) => string;
}) {
  return (
    <table style={{ borderCollapse: "collapse", fontSize: "13px" }}>
      <thead>
        <tr>
          <th style={{ padding: "4px 8px", border: "1px solid #ccc" }} />
          {rows.map((r) => (
            <th key={r} style={{ padding: "4px 8px", border: "1px solid #ccc" }}>
              {r}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {cells.map((row, i) => (
          <tr key={rows[i]}>
            <th style={{ padding: "4px 8px", border: "1px solid #ccc" }}>{rows[i]}</th>
            {row.map((v, j) => (
              <td key={j} style={{ padding: "4px 8px", border: "1px solid #ccc", textAlign: "right" }}>
                {format(v)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function App() {
  const {
    state,
    run,
    selectCandidate,
    confirmPartial,
    retryItem,
    editItem,
    setCity,
    retryFlaggedInCity,
    toggleSkip,
  } = useGeocode();
  const start = useStartPoint();
  const matrixHook = useMatrix();
  const [debug] = useState(() => new URLSearchParams(location.search).has("debug"));

  const stopPoints = useMemo<MatrixPoint[]>(
    () =>
      usableStopRows(state.items)
        .filter((r) => r.coordinate)
        .map((r) => ({
          id: String(r.id),
          lat: r.coordinate!.latitude,
          lon: r.coordinate!.longitude,
        })),
    [state.items]
  );

  const readiness = canCalculateRoute({
    startResolved: start.state.status === "resolved" && !!start.state.coordinate,
    rows: state.items.map((r) => ({
      is_blank: r.is_blank,
      status: r.status,
      confirmed: r.confirmed,
      needs_check: r.needs_check,
      skipped: r.skipped,
    })),
  });
  const busy = state.loading || start.state.status === "resolving" || matrixHook.state.loading;

  function calcRoute() {
    if (!readiness.ok) return;
    if (!start.state.coordinate) return;
    matrixHook.calculate([
      { id: "start", lat: start.state.coordinate.latitude, lon: start.state.coordinate.longitude },
      ...stopPoints,
    ]);
  }

  const result = matrixHook.state.result;
  const stopCount = result ? result.ids.length - 1 : null;
  const tableRows = result ? result.ids : [];

  return (
    <div style={{ maxWidth: "800px", margin: "0 auto", padding: "16px" }}>
      <h1>Route Planner</h1>
      <AddressInput
        onRun={run}
        loading={state.loading}
        city={state.city}
        onCityChange={setCity}
        citySuggestion={state.citySuggestion}
        onAcceptSuggestion={() =>
          state.citySuggestion ? setCity(state.citySuggestion) : undefined
        }
      />
      {state.error && <div style={{ color: "red", marginTop: "8px" }}>{state.error}</div>}
      <ResultsList
        items={state.items}
        processed={state.processed}
        total={state.total}
        onSelectCandidate={selectCandidate}
        onConfirmPartial={confirmPartial}
        onRetry={retryItem}
        onEdit={editItem}
        onRetryFlaggedInCity={retryFlaggedInCity}
        onToggleSkip={toggleSkip}
      />
      <StartPointInput
        start={start.state}
        onTextChange={start.setText}
        onResolve={() => start.run(state.city)}
        onSelectCandidate={start.selectCandidate}
        onConfirmPartial={start.confirmPartial}
      />
      <button
        onClick={calcRoute}
        disabled={!readiness.ok || busy}
        style={{ padding: "12px 16px", fontSize: "16px" }}
      >
        {matrixHook.state.loading ? "Calculating..." : "Calculate route"}
      </button>
      {!readiness.ok && !busy && (
        <span style={{ color: "#777", marginLeft: "8px" }}>{readiness.reason}</span>
      )}

      {matrixHook.state.error && (
        <div style={{ color: "red", marginTop: "8px" }}>{matrixHook.state.error}</div>
      )}

      {result && (
        <div style={{ marginTop: "16px" }}>
          <div>
            <strong>Distances calculated for {stopCount} stop{stopCount === 1 ? "" : "s"}.</strong>
          </div>
          {result.note && <div style={{ color: "#555", marginTop: "4px" }}>{result.note}</div>}
          {result.problems.length > 0 && (
            <ul style={{ marginTop: "8px" }}>
              {result.problems.map((p, i) => (
                <li key={i} style={{ color: "#b26a00" }}>
                  {p.message}
                </li>
              ))}
            </ul>
          )}
          {debug && (
            <div style={{ marginTop: "12px" }}>
              <div style={{ marginBottom: "8px" }}>
                <strong>Matrix — typical drive time, minutes</strong>
                <MatrixTable rows={tableRows} cells={result.durations_s} format={minutes} />
              </div>
              <div>
                <strong>Matrix — distance, km</strong>
                <MatrixTable rows={tableRows} cells={result.distances_m} format={kilometers} />
              </div>
            </div>
          )}
        </div>
      )}

      <footer
        style={{
          marginTop: "32px",
          paddingTop: "8px",
          borderTop: "1px solid #eee",
          color: "#777",
          fontSize: "12px",
        }}
      >
        Distances and drive times are estimates. Map data © OpenStreetMap
        contributors. Routes by OSRM.
      </footer>
    </div>
  );
}