import { useStartPoint } from "../hooks/useStartPoint";

interface Props {
  start: ReturnType<typeof useStartPoint>["state"];
  onTextChange: (text: string) => void;
  onResolve: () => void;
  onSelectCandidate: (index: number) => void;
  onConfirmPartial: () => void;
}

export function StartPointInput({
  start,
  onTextChange,
  onResolve,
  onSelectCandidate,
  onConfirmPartial,
}: Props) {
  return (
    <div style={{ marginTop: "16px", marginBottom: "16px" }}>
      <label htmlFor="start-point-field" style={{ display: "block", marginBottom: "4px" }}>
        Start point
      </label>
      <div style={{ display: "flex", gap: "8px" }}>
        <input
          id="start-point-field"
          value={start.text}
          onChange={(e) => onTextChange(e.target.value)}
          placeholder="Where the drive starts, e.g. Київ, Хрещатик 1"
          style={{ flex: 1, fontSize: "16px" }}
        />
        <button onClick={onResolve} disabled={start.status === "resolving" || !start.text.trim()}>
          {start.status === "resolving" ? "Checking..." : "Check"}
        </button>
      </div>

      {start.status === "resolved" && start.display_name && (
        <div style={{ color: "#2e7d32", marginTop: "4px" }}>{start.display_name}</div>
      )}
      {start.status === "partial" && (
        <div style={{ marginTop: "4px" }}>
          <span style={{ color: "#555" }}>{start.message}</span>
          <button onClick={onConfirmPartial} style={{ marginLeft: "8px" }}>
            Confirm
          </button>
          {start.display_name && (
            <span style={{ color: "#2e7d32", marginLeft: "8px" }}>{start.display_name}</span>
          )}
        </div>
      )}
      {start.status === "ambiguous" && (
        <div style={{ marginTop: "4px" }}>
          <div style={{ color: "#555" }}>{start.message}</div>
          {start.candidates?.map((c, i) => (
            <button
              key={i}
              onClick={() => onSelectCandidate(i)}
              style={{ display: "block", marginTop: "4px" }}
            >
              {c.display_name}
            </button>
          ))}
        </div>
      )}
      {(start.status === "not_found" || start.status === "error") && (
        <div style={{ color: "red", marginTop: "4px" }}>{start.message}</div>
      )}
    </div>
  );
}