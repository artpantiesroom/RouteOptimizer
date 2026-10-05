import { useEffect, useState } from "react";
import type { GeocodeResultItem, UnitKind } from "../types/types";
import { UNIT_KIND_LABELS } from "../types/types";

// User-facing copy. Must stay free of technical terms (geocoding, provider, HTTP).
const RETRYABLE_ERROR_MESSAGE = "Could not check this address right now. Try again.";
const SETUP_ERROR_MESSAGE =
  "The address search service rejected the request. This is a setup problem, not your address.";
const OFFLINE_ERROR_MESSAGE =
  "Could not reach the address search. Your edit is kept, try again when you are back online.";
const APPROXIMATE_COPY = "Street found, house not found. The pin is approximate.";

interface Props {
  item: GeocodeResultItem;
  onSelectCandidate: (candidateIndex: number) => void;
  onConfirmPartial: () => void;
  onRetry: () => void;
  onEdit: (newText: string) => void;
  // Optional so a row can be rendered in isolation (tests, storybook) without
  // the whole retry flow. The button is hidden when it is missing.
  onRetryInCity?: (city: string) => void;
}

/** "Apartment 9", or a hedged reading when the unit was guessed from "15-9". */
function unitLine(item: GeocodeResultItem): string | null {
  if (!item.unit) return null;
  const kind = (item.unit_kind ?? "apartment") as UnitKind;
  const label = UNIT_KIND_LABELS[kind] ?? "Unit";
  if (item.unit_inferred) {
    const house = item.house ? `house ${item.house}, ` : "";
    return `Read as ${house}${label.toLowerCase()} ${item.unit}. Please check.`;
  }
  return `${label} ${item.unit}`;
}

export function ResultItem({
  item,
  onSelectCandidate,
  onConfirmPartial,
  onRetry,
  onEdit,
  onRetryInCity,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.trimmed);

  // Keep the field in step when the row is re-run from elsewhere.
  useEffect(() => {
    if (!editing) setDraft(item.trimmed);
  }, [item.trimmed, editing]);

  const isRetryable = item.error_kind === "retryable";
  const errorMessage = item.offline
    ? OFFLINE_ERROR_MESSAGE
    : item.error_kind === "setup"
      ? SETUP_ERROR_MESSAGE
      : item.error_kind === "retryable"
        ? RETRYABLE_ERROR_MESSAGE
        : "Something went wrong. Try again.";

  const interpretation = unitLine(item);
  const busy = item.retrying || item.rechecking;

  function submitEdit() {
    const next = draft.trim();
    if (!next || next === item.trimmed) {
      setEditing(false);
      setDraft(item.trimmed);
      return;
    }
    onEdit(next);
    setEditing(false);
  }

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
        {editing ? (
          <span>
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submitEdit();
                if (e.key === "Escape") {
                  setDraft(item.trimmed);
                  setEditing(false);
                }
              }}
              aria-label="Edit address"
              autoFocus
              style={{ width: "70%", fontSize: "14px", marginRight: "6px" }}
            />
            <button onClick={submitEdit} disabled={busy} style={{ marginRight: "4px" }}>
              Run again
            </button>
            <button
              onClick={() => {
                setDraft(item.trimmed);
                setEditing(false);
              }}
            >
              Cancel
            </button>
          </span>
        ) : (
          <span>
            <strong>{item.original}</strong>
            {item.is_duplicate && <span style={{ color: "#666" }}> (duplicate)</span>}
            {!item.is_blank && (
              <button
                onClick={() => setEditing(true)}
                disabled={busy}
                style={{ marginLeft: "8px" }}
              >
                Edit
              </button>
            )}
            {busy && (
              <span style={{ marginLeft: "8px", color: "#666" }}>
                {item.rechecking ? "Checking..." : "Retrying..."}
              </span>
            )}
          </span>
        )}
      </div>

      {item.searched_as && item.searched_as !== item.original.trim() && (
        <div style={{ color: "#555" }}>Searched as: {item.searched_as}</div>
      )}
      {interpretation && <div style={{ color: "#555" }}>{interpretation}</div>}

      {item.status === "resolved" && (
        <div style={{ color: "green" }}>
          Resolved: {item.display_name}
        </div>
      )}
      {item.status === "partial" && (
        <div style={{ color: "#b8860b" }}>
          {item.message || APPROXIMATE_COPY}
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
          {/* One wrong address is not a choice to make, it is one suggestion
              to accept or leave, so it gets a single action. */}
          {item.candidates.length === 1 ? (
            <>
              <div>{item.message || "One match found - accept it or check the address:"}</div>
              <ul style={{ margin: "4px 0" }}>
                {item.candidates.map((c, i) => (
                  <li key={i}>
                    <button onClick={() => onSelectCandidate(i)} style={{ marginRight: "8px" }}>
                      Use this match
                    </button>
                    {c.display_name}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <>
              <div>More than one match - pick one:</div>
              <ul style={{ margin: "4px 0" }}>
                {item.candidates.map((c, i) => (
                  <li key={i}>
                    <button onClick={() => onSelectCandidate(i)} style={{ marginRight: "8px" }}>
                      Choose
                    </button>
                    {c.display_name}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
      {item.status === "not_found" && (
        <div style={{ color: "red" }}>
          {item.message || "Not found - check spelling/address"}
        </div>
      )}
      {item.status === "error" && (
        <div style={{ color: "red" }}>
          {errorMessage}
          {isRetryable && (
            <button onClick={onRetry} disabled={busy} style={{ marginLeft: "8px" }}>
              {item.retrying ? "Retrying..." : "Retry"}
            </button>
          )}
        </div>
      )}

      {item.needs_check && (
        <div style={{ color: "#b8860b" }}>
          Needs a check{item.needs_check_reason ? `: ${item.needs_check_reason}` : "."}
          {item.retry_city && !busy && onRetryInCity && (
            <button
              onClick={() => onRetryInCity(item.retry_city as string)}
              style={{ marginLeft: "8px" }}
            >
              Search again in {item.retry_city}
            </button>
          )}
        </div>
      )}

      {item.house && item.found_house && item.found_house !== item.house && (
        <div style={{ color: "#b8860b" }}>
          House {item.house} was asked for, the match is {item.found_house}.
        </div>
      )}

      {item.scope_message && (
        <div style={{ color: "#666" }}>{item.scope_message}</div>
      )}
    </div>
  );
}