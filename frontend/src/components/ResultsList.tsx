import type { GeocodeResultItem } from "../types/types";
import { ResultItem } from "./ResultItem";

interface Props {
  items: GeocodeResultItem[];
  processed: number;
  total: number;
  onSelectCandidate: (itemId: number, candidateIndex: number) => void;
  onConfirmPartial: (itemId: number) => void;
  onRetry: (itemId: number) => void;
  onEdit: (itemId: number, newText: string) => void;
  onRetryFlaggedInCity: (city: string) => void;
  onToggleSkip: (itemId: number) => void;
}

export function ResultsList({
  items,
  processed,
  total,
  onSelectCandidate,
  onConfirmPartial,
  onRetry,
  onEdit,
  onRetryFlaggedInCity,
  onToggleSkip,
}: Props) {
  const nonBlank = items.filter((i) => !i.is_blank).length;
  const recognized = items.filter((i) => {
    if (i.is_blank) return false;
    // A row that needs a check is not recognized, however it is marked: the
    // point of the flag is that it has not proven where it is.
    if (i.needs_check) return false;
    if (i.status === "resolved") return true;
    if (i.status === "ambiguous" && i.selectedCandidateIndex !== undefined) return true;
    return false;
  }).length;

  const needsCheck = items.filter((i) => i.needs_check).length;
  const skipped = items.filter((i) => i.skipped).length;
  // One city to offer: the city the flagged rows should have been in.
  const retryCities = [
    ...new Set(items.filter((i) => i.needs_check && i.retry_city).map((i) => i.retry_city)),
  ];
  const offerCity = retryCities.length === 1 ? retryCities[0] : null;

  const displayTotal = total > 0 ? total : nonBlank;

  return (
    <div style={{ marginTop: "16px" }}>
      <div>
        {processed} of {nonBlank} processed | {recognized} of {displayTotal} recognized
        {needsCheck > 0 ? ` | ${needsCheck} need a check` : ""}
        {skipped > 0 ? ` | ${skipped} skipped` : ""}
      </div>
      {needsCheck > 0 && offerCity && (
        <div style={{ color: "#b8860b" }}>
          Some rows were found somewhere else or too far from the rest.
          <button onClick={() => onRetryFlaggedInCity(offerCity)} style={{ marginLeft: "8px" }}>
            Search the {needsCheck} again in {offerCity}
          </button>
        </div>
      )}
      {items.map((item) => (
        <ResultItem
          key={item.id}
          item={item}
          onSelectCandidate={(idx) => onSelectCandidate(item.id, idx)}
          onConfirmPartial={() => onConfirmPartial(item.id)}
          onRetry={() => onRetry(item.id)}
          onEdit={(newText) => onEdit(item.id, newText)}
          onRetryInCity={(city) => onRetryFlaggedInCity(city)}
          onToggleSkip={() => onToggleSkip(item.id)}
        />
      ))}
    </div>
  );
}