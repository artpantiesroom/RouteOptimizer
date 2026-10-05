import type { GeocodeResultItem } from "../types/types";
import { ResultItem } from "./ResultItem";

interface Props {
  items: GeocodeResultItem[];
  processed: number;
  total: number;
  onSelectCandidate: (itemId: number, candidateIndex: number) => void;
  onConfirmPartial: (itemId: number) => void;
  onRetry: (itemId: number) => void;
}

export function ResultsList({
  items,
  processed,
  total,
  onSelectCandidate,
  onConfirmPartial,
  onRetry,
}: Props) {
  const nonBlank = items.filter((i) => !i.is_blank).length;
  const recognized = items.filter((i) => {
    if (i.is_blank) return false;
    if (i.status === "resolved") return true;
    if (i.status === "ambiguous" && i.selectedCandidateIndex !== undefined) return true;
    return false;
  }).length;

  const displayTotal = total > 0 ? total : nonBlank;

  return (
    <div style={{ marginTop: "16px" }}>
      <div>
        {processed} of {nonBlank} processed | {recognized} of {displayTotal} recognized
      </div>
      {items.map((item) => (
        <ResultItem
          key={item.id}
          item={item}
          onSelectCandidate={(idx) => onSelectCandidate(item.id, idx)}
          onConfirmPartial={() => onConfirmPartial(item.id)}
          onRetry={() => onRetry(item.id)}
        />
      ))}
    </div>
  );
}
