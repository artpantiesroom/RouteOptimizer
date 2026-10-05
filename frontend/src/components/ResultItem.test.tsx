import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ResultItem } from "./ResultItem";
import type { GeocodeResultItem } from "../types/types";

function makeItem(overrides: Partial<GeocodeResultItem> = {}): GeocodeResultItem {
  return {
    id: 0,
    original: "Some Street 1",
    trimmed: "Some Street 1",
    is_blank: false,
    is_duplicate: false,
    duplicate_of: null,
    status: "not_found",
    ...overrides,
  } as GeocodeResultItem;
}

const noop = () => {};
const onEdit = () => {};

describe("ResultItem - searched query", () => {
  it("shows the text that was actually searched", () => {
    render(
      <ResultItem
        item={makeItem({
          status: "resolved",
          display_name: "ok",
          original: "ул. Покровская, 8, Киев",
          searched_as: "вулиця Покровская, 8, Київ",
        })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    expect(screen.getByText(/Searched as:/)).toHaveTextContent(
      "Searched as: вулиця Покровская, 8, Київ"
    );
  });

  it("hides the line when nothing was changed", () => {
    render(
      <ResultItem
        item={makeItem({
          status: "resolved",
          display_name: "ok",
          original: "Same as typed",
          trimmed: "Same as typed",
          searched_as: "Same as typed",
        })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    expect(screen.queryByText(/Searched as:/)).toBeNull();
  });
});

describe("ResultItem - unit interpretation", () => {
  it("hedges an inferred apartment", () => {
    render(
      <ResultItem
        item={makeItem({
          status: "resolved",
          display_name: "ok",
          house: "15",
          unit: "9",
          unit_kind: "apartment",
          unit_inferred: true,
        })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    expect(screen.getByText(/Read as house 15, apartment 9\. Please check\./)).toBeInTheDocument();
  });

  it("states an explicit apartment without hedging", () => {
    render(
      <ResultItem
        item={makeItem({
          status: "resolved",
          display_name: "ok",
          house: "15",
          unit: "9",
          unit_kind: "apartment",
          unit_inferred: false,
        })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    expect(screen.getByText("Apartment 9")).toBeInTheDocument();
    expect(screen.queryByText(/Read as/)).toBeNull();
  });

  it("uses the right label for an office", () => {
    render(
      <ResultItem
        item={makeItem({ status: "resolved", display_name: "ok", unit: "12", unit_kind: "office" })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    expect(screen.getByText("Office 12")).toBeInTheDocument();
  });

  it("shows nothing when there is no unit", () => {
    render(
      <ResultItem
        item={makeItem({ status: "resolved", display_name: "ok" })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    expect(screen.queryByText(/Apartment/)).toBeNull();
  });
});

describe("ResultItem - approximate street match", () => {
  it("uses the agreed plain wording", () => {
    render(
      <ResultItem
        item={makeItem({
          status: "partial",
          message: "Street found, house not found. The pin is approximate.",
        })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    expect(
      screen.getByText("Street found, house not found. The pin is approximate.")
    ).toBeInTheDocument();
  });

  it("falls back to that wording if no message arrives", () => {
    render(
      <ResultItem
        item={makeItem({ status: "partial" })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    expect(
      screen.getByText("Street found, house not found. The pin is approximate.")
    ).toBeInTheDocument();
  });

  it("still asks for explicit confirmation", () => {
    render(
      <ResultItem
        item={makeItem({ status: "partial" })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    expect(screen.getByRole("button", { name: "Confirm as is" })).toBeInTheDocument();
  });

  it("shows one candidate rather than a list", () => {
    render(
      <ResultItem
        item={makeItem({
          status: "partial",
          candidates: [{ display_name: "Street", latitude: 1, longitude: 2 }],
        })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    expect(screen.queryByRole("button", { name: "Choose" })).toBeNull();
  });
});

describe("ResultItem - city scope notice", () => {
  it("reports how many matches were ignored", () => {
    render(
      <ResultItem
        item={makeItem({
          status: "resolved",
          display_name: "ok",
          dropped_candidates: 3,
          scope_message: "3 matches were ignored (outside Київ).",
        })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    expect(screen.getByText("3 matches were ignored (outside Київ).")).toBeInTheDocument();
  });

  it("says nothing when nothing was dropped", () => {
    render(
      <ResultItem
        item={makeItem({ status: "resolved", display_name: "ok", dropped_candidates: 0 })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    expect(screen.queryByText(/ignored/)).toBeNull();
  });

  it("shows the notice for a not_found row too", () => {
    render(
      <ResultItem
        item={makeItem({
          status: "not_found",
          dropped_candidates: 2,
          scope_message: "2 matches were ignored (outside Київ).",
        })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    expect(screen.getByText("2 matches were ignored (outside Київ).")).toBeInTheDocument();
  });
});

describe("ResultItem - candidate list", () => {
  const ambiguous = makeItem({
    status: "ambiguous",
    candidates: [
      { display_name: "First match", latitude: 1, longitude: 1 },
      { display_name: "Second match", latitude: 2, longitude: 2 },
    ],
  });

  it("separates the Choose button from the candidate text", () => {
    render(
      <ResultItem
        item={ambiguous}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    const button = screen.getAllByRole("button", { name: "Choose" })[0];
    // A real gap is required: adjacent text nodes are announced as one string.
    expect(button.style.marginRight).toBe("8px");
  });

  it("lists each candidate with its own button", async () => {
    const onSelectCandidate = vi.fn();
    render(
      <ResultItem
        item={ambiguous}
        onSelectCandidate={onSelectCandidate}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    const buttons = screen.getAllByRole("button", { name: "Choose" });
    expect(buttons).toHaveLength(2);
    expect(screen.getByText("First match")).toBeInTheDocument();
    expect(screen.getByText("Second match")).toBeInTheDocument();

    await userEvent.click(buttons[1]);
    expect(onSelectCandidate).toHaveBeenCalledWith(1);
  });
});

describe("ResultItem - editing in place", () => {
  it("opens an editor prefilled with the current text", async () => {
    render(
      <ResultItem
        item={makeItem({ original: "Old text", trimmed: "Old text" })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    const input = screen.getByLabelText("Edit address") as HTMLInputElement;
    expect(input.value).toBe("Old text");
  });

  it("re-runs the row with the edited text", async () => {
    const handler = vi.fn();
    render(
      <ResultItem
        item={makeItem({ original: "Old text" })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={handler}
      />
    );
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    const input = screen.getByLabelText("Edit address");
    await userEvent.clear(input);
    await userEvent.type(input, "New text");
    await userEvent.click(screen.getByRole("button", { name: "Run again" }));
    expect(handler).toHaveBeenCalledWith("New text");
  });

  it("closes without re-running when the text is unchanged", async () => {
    const handler = vi.fn();
    render(
      <ResultItem
        item={makeItem({ original: "Same text", trimmed: "Same text" })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={handler}
      />
    );
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Run again" }));
    expect(handler).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Edit address")).toBeNull();
  });

  it("cancels back to the original text", async () => {
    const handler = vi.fn();
    render(
      <ResultItem
        item={makeItem({ original: "Same text", trimmed: "Same text" })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={handler}
      />
    );
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    const input = screen.getByLabelText("Edit address");
    await userEvent.clear(input);
    await userEvent.type(input, "Discarded");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(handler).not.toHaveBeenCalled();
    expect(screen.getByText("Same text")).toBeInTheDocument();
  });

  it("disables editing for a blank row", () => {
    render(
      <ResultItem
        item={makeItem({ is_blank: true, original: "" })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });

  it("shows progress while the row is being re-checked", () => {
    render(
      <ResultItem
        item={makeItem({ status: "resolved", display_name: "ok", rechecking: true })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    expect(screen.getByText("Checking...")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
  });
});

describe("ResultItem - error wording", () => {
  it("uses plain wording for a retryable failure", () => {
    render(
      <ResultItem
        item={makeItem({ status: "error", error_kind: "retryable" })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    expect(
      screen.getByText("Could not check this address right now. Try again.")
    ).toBeInTheDocument();
  });

  it("explains a setup failure without offering a retry", () => {
    render(
      <ResultItem
        item={makeItem({ status: "error", error_kind: "setup" })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    expect(
      screen.getByText(
        "The address search service rejected the request. This is a setup problem, not your address."
      )
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("keeps the edit visible when the service was unreachable", () => {
    render(
      <ResultItem
        item={makeItem({
          status: "error",
          error_kind: "retryable",
          offline: true,
          original: "Edited",
          trimmed: "Edited",
        })}
        onSelectCandidate={noop}
        onConfirmPartial={noop}
        onRetry={noop}
        onEdit={onEdit}
      />
    );
    expect(
      screen.getByText(
        "Could not reach the address search. Your edit is kept, try again when you are back online."
      )
    ).toBeInTheDocument();
    expect(screen.getByText("Edited")).toBeInTheDocument();
  });
});