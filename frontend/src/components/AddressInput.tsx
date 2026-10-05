import { useState } from "react";

interface Props {
  onRun: (text: string) => void;
  loading: boolean;
  city: string;
  onCityChange: (city: string) => void;
  /** City the resolved rows agreed on, when the field is still blank. */
  citySuggestion?: string | null;
  onAcceptSuggestion?: () => void;
}

export function AddressInput({
  onRun,
  loading,
  city,
  onCityChange,
  citySuggestion,
  onAcceptSuggestion,
}: Props) {
  const [text, setText] = useState("");
  return (
    <div>
      <label htmlFor="city-field" style={{ display: "block", marginBottom: "4px" }}>
        City
      </label>
      <input
        id="city-field"
        value={city}
        onChange={(e) => onCityChange(e.target.value)}
        placeholder="Filled in from the first address; edit it if needed"
        style={{ width: "100%", fontSize: "16px", marginBottom: "12px" }}
      />
      {citySuggestion && !city.trim() && onAcceptSuggestion && (
        <div style={{ color: "#555", marginBottom: "12px" }}>
          Most addresses resolved in {citySuggestion}.
          <button onClick={onAcceptSuggestion} style={{ marginLeft: "8px" }}>
            Use {citySuggestion}
          </button>
        </div>
      )}
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Paste addresses, one per line"
        rows={8}
        style={{ width: "100%", fontSize: "16px" }}
        aria-label="Address list"
      />
      <button
        onClick={() => onRun(text)}
        disabled={loading || text.trim().length === 0}
        style={{ marginTop: "8px", padding: "12px 16px", fontSize: "16px" }}
      >
        {loading ? "Processing..." : "Recognize addresses"}
      </button>
    </div>
  );
}