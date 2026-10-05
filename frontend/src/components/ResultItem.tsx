import type { GeocodeResultItem } from "../types/types";

// User-facing copy. Must stay free of technical terms (geocoding, provider, HTTP).
const RETRYABLE_ERROR_MESSAGE = "Could not check this address right now. Try again.";
const SETUP_ERROR_MESSAGE =
  "The address search service rejected the request. This is a setup problem, not your address.";

interface Props {
  item: GeocodeResultItem;
  onSelectCandidate: (candidateIndex: number) => void;
  onConfirmPartial: () => void;
  onRetry: () => void;
}

export function ResultItem({
  item,
  onSelectCandidate,
  onConfirmPartial,
  onRetry,
}: Props) {
  const isRetryable = item.error_kind === "retryable";
  const errorMessage = item.error_kind
    ? item.error_kind === "setup"
      ? SETUP_ERROR_MESSAGE
      : RETRYABLE_ERROR_MESSAGE
    : "Something went wrong. Try again.";

  return (
    <div
      style={{
        border: "1px solid #ccc",
        padding: "8px",
        marginBottom: "8px",
        borderRadius: "4px",
      }}
    >
      <div>
        <strong>{item.original}</strong>
        {item.is_duplicate && <span style={{ color: "#666" }}> (duplicate)</span>}
      </div>
      {item.status === "resolved" && (
        <div style={{ color: "green" }}>
          Resolved: {item.display_name}
        </div>
      )}
      {item.status === "partial" && (
        <div style={{ color: "#b8860b" }}>
          Partial: {item.message || item.error_message}
          {!item.confirmed && (
            <button onClick={onConfirmPartial} style={{ marginLeft: "8px" }}>
              Confirm as is
            </button>
          )}
          {item.confirmed && <span style={{ marginLeft: "8px" }}>Confirmed</span>}
        </div>
      )}
      {item.status === "ambiguous" && item.candidates && (
        <div style={{ color: "#0000cd" }}>
          Ambiguous - pick one:
          <ul style={{ margin: "4px 0" }}>
            {item.candidates.map((c, i) => (
              <li key={i}>
                <button onClick={() => onSelectCandidate(i)} style={{ marginRight: "4px" }}>
                  Choose
                </button>
                {c.display_name}
              </li>
            ))}
          </ul>
        </div>
      )}
      {item.status === "not_found" && (
        <div style={{ color: "red" }}>Not found - check spelling/address</div>
      )}
      {item.status === "error" && (
        <div style={{ color: "red" }}>
          {errorMessage}
          {isRetryable && (
            <button
              onClick={onRetry}
              disabled={item.retrying}
              style={{ marginLeft: "8px" }}
            >
              {item.retrying ? "Retrying..." : "Retry"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}