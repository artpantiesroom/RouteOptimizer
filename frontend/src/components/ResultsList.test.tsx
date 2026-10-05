import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ResultsList } from "./ResultsList";
import type { GeocodeResultItem } from "../types/types";

/** Fixtures are invented; only the city names are real. */
function makeItem(overrides: Partial<GeocodeResultItem>): GeocodeResultItem {
  return {
    id: 0,
    original: "вулиця Тестова, 1",
    trimmed: "вулиця Тестова, 1",
    is_blank: false,
    is_duplicate: false,
    duplicate_of: null,
    status: "resolved",
    ...overrides,
  } as GeocodeResultItem;
}

const noop = () => {};
const noopItem = () => {};

function renderList(items: GeocodeResultItem[], onRetryFlaggedInCity = noop) {
  render(
    <ResultsList
      items={items}
      processed={items.length}
      total={items.length}
      onSelectCandidate={noopItem}
      onConfirmPartial={noopItem}
      onRetry={noopItem}
      onEdit={noopItem}
      onRetryFlaggedInCity={onRetryFlaggedInCity}
    />
  );
}

describe("ResultsList - recognized count", () => {
  it("does not count a row that needs a check as recognized", () => {
    renderList([
      makeItem({ id: 0, status: "resolved" }),
      makeItem({ id: 1, status: "resolved", needs_check: true, retry_city: "Київ" }),
    ]);
    expect(screen.getByText(/recognized/)).toHaveTextContent("2 of 2 processed | 1 of 2 recognized");
  });

  it("counts a row the user picked a candidate for", () => {
    renderList([
      makeItem({ id: 0, status: "ambiguous", selectedCandidateIndex: 0 }),
      makeItem({ id: 1, status: "ambiguous" }),
    ]);
    expect(screen.getByText(/recognized/)).toHaveTextContent("2 of 2 processed | 1 of 2 recognized");
  });
});

describe("ResultsList - retry the flagged rows", () => {
  it("offers one button for all flagged rows in the same city", async () => {
    const onRetryFlaggedInCity = vi.fn();
    renderList(
      [
        makeItem({ id: 0, status: "resolved" }),
        makeItem({ id: 1, status: "not_found", needs_check: true, retry_city: "Київ" }),
        makeItem({ id: 2, status: "not_found", needs_check: true, retry_city: "Київ" }),
      ],
      onRetryFlaggedInCity
    );
    await userEvent.click(screen.getByRole("button", { name: "Search the 2 again in Київ" }));
    expect(onRetryFlaggedInCity).toHaveBeenCalledWith("Київ");
  });

  it("stays quiet when the flagged rows point at different cities", () => {
    renderList([
      makeItem({ id: 0, status: "not_found", needs_check: true, retry_city: "Київ" }),
      makeItem({ id: 1, status: "not_found", needs_check: true, retry_city: "Львів" }),
    ]);
    expect(screen.queryByRole("button", { name: /Search the/ })).toBeNull();
    expect(screen.getByText(/need a check/)).toHaveTextContent("2 need a check");
  });

  it("shows no banner when nothing needs a check", () => {
    renderList([makeItem({ id: 0, status: "resolved" })]);
    expect(screen.queryByRole("button", { name: /Search the/ })).toBeNull();
  });
});