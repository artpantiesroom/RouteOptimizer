import { useGeocode } from "./hooks/useGeocode";
import { AddressInput } from "./components/AddressInput";
import { ResultsList } from "./components/ResultsList";

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
  } = useGeocode();
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
      />
    </div>
  );
}