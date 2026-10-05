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