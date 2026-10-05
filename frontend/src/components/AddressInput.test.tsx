import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AddressInput } from "./AddressInput";

function Harness({ initialCity = "" }: { initialCity?: string }) {
  const [city, setCity] = useState(initialCity);
  return <AddressInput onRun={() => {}} loading={false} city={city} onCityChange={setCity} />;
}

describe("AddressInput - City field", () => {
  it("sits above the address list", () => {
    render(<Harness />);
    const city = screen.getByLabelText("City");
    const list = screen.getByLabelText("Address list");
    expect(city.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows a value the parent supplied", () => {
    render(<Harness initialCity="Київ" />);
    expect((screen.getByLabelText("City") as HTMLInputElement).value).toBe("Київ");
  });

  it("reports typing back to the parent", async () => {
    const onCityChange = vi.fn();
    render(
      <AddressInput onRun={() => {}} loading={false} city="" onCityChange={onCityChange} />
    );
    await userEvent.type(screen.getByLabelText("City"), "Львів");
    expect(onCityChange).toHaveBeenCalled();
    expect(onCityChange.mock.calls.map((c) => c[0]).join("")).toBe("Львів");
  });

  it("is editable after being prefilled", async () => {
    render(<Harness initialCity="Київ" />);
    const city = screen.getByLabelText("City");
    await userEvent.clear(city);
    await userEvent.type(city, "Одеса");
    expect((city as HTMLInputElement).value).toBe("Одеса");
  });

  it("explains that the value comes from the address list", () => {
    render(<Harness />);
    expect(screen.getByPlaceholderText(/first address/i)).toBeInTheDocument();
  });
});
describe("AddressInput - suggested city", () => {
  function SuggestionHarness({ city = "" }: { city?: string }) {
    const [value, setValue] = useState(city);
    return (
      <AddressInput
        onRun={() => {}}
        loading={false}
        city={value}
        onCityChange={setValue}
        citySuggestion="Київ"
        onAcceptSuggestion={() => setValue("Київ")}
      />
    );
  }

  it("offers the suggested city when the field is blank", () => {
    render(<SuggestionHarness />);
    expect(screen.getByText(/Most addresses resolved in Київ/)).toBeInTheDocument();
  });

  it("fills the field when the suggestion is accepted", async () => {
    render(<SuggestionHarness />);
    await userEvent.click(screen.getByRole("button", { name: "Use Київ" }));
    expect((screen.getByLabelText("City") as HTMLInputElement).value).toBe("Київ");
  });

  it("stops offering once the field has a city", () => {
    render(<SuggestionHarness city="Львів" />);
    expect(screen.queryByRole("button", { name: "Use Київ" })).toBeNull();
  });

  it("shows nothing without a suggestion", () => {
    render(<Harness />);
    expect(screen.queryByRole("button", { name: /Use / })).toBeNull();
  });
});
