import { useState } from "react";

interface Props {
  onRun: (text: string) => void;
  loading: boolean;
}

export function AddressInput({ onRun, loading }: Props) {
  const [text, setText] = useState("");
  return (
    <div>
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
