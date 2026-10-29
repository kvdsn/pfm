import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DirectorLoanSection } from "./DirectorLoan";
import type { DirectorLoan } from "./types";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({
  api: apiMock,
  errorMessage: (cause: unknown, fallback: string) => (cause instanceof Error ? cause.message : fallback),
}));

const loan: DirectorLoan = {
  start: "2026-01-01", rate: 3.5, balance: "20000.00", interest_to_date: "110.27", interest_paid: "0.00",
  years: [{
    year: "2026", opening: "0.00", transfers: "20000.00", transfers_projected: "8000.00", closing: "28000.00",
    interest: "250.00", interest_paid: "0.00", interest_planned: "0.00", interest_unpaid: "250.00",
  }],
  movements: [
    { date: "2026-08-02", amount: "20000.00", projected: false, balance: "20000.00" },
    { date: "2026-12-02", amount: "8000.00", projected: true, balance: "28000.00" },
  ],
  payments: [],
  notes: ["The category for the interest is not chosen."],
};

beforeEach(() => { apiMock.mockReset(); });
afterEach(cleanup);

const renderSection = (data: DirectorLoan | null = loan) => {
  const props = { onLoan: vi.fn(), onChanged: vi.fn(), onNotice: vi.fn() };
  render(<DirectorLoanSection loan={data} rules={<p>Rules form</p>} {...props} />);
  return props;
};

describe("DirectorLoanSection", () => {
  it("shows the heading line, the notes and the year table", () => {
    renderSection();
    const region = within(screen.getByRole("region", { name: "Director loan" }));
    expect(region.getByText(/interest so far/)).toBeInTheDocument();
    expect(region.getByText("€ 20.000,00", { selector: "strong" })).toBeInTheDocument();
    expect(region.getByText("The category for the interest is not chosen.")).toBeInTheDocument();
    const row = within(region.getByRole("table", { name: "Loan per year" })).getAllByRole("row")[1];
    expect(row).toHaveTextContent("2026");
    expect(row).toHaveTextContent("+ € 8.000,00 projected");
    expect(row).toHaveTextContent("€ 28.000,00");
    expect(region.getByText("Rules form")).toBeInTheDocument();
  });

  it("lists the movements only when opened", () => {
    renderSection();
    expect(screen.queryByRole("table", { name: "Loan movements" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show the movements" }));
    expect(within(screen.getByRole("table", { name: "Loan movements" })).getAllByRole("row")).toHaveLength(3);
  });

  it("plans the interest with the unpaid amount as default", async () => {
    const next = { ...loan, notes: [] };
    apiMock.mockResolvedValue(next);
    const props = renderSection();
    fireEvent.click(screen.getByRole("button", { name: "Plan interest 2026" }));
    expect(screen.getByLabelText("Date")).toHaveValue("2026-12-31");
    expect(screen.getByLabelText("Amount")).toHaveValue("250.00");
    fireEvent.change(screen.getByLabelText(/Note/), { target: { value: "Year 2026" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(props.onLoan).toHaveBeenCalledWith(next));
    expect(apiMock).toHaveBeenCalledWith("/api/forecast/director-loan/interest", {
      method: "POST", json: { date: "2026-12-31", amount: "250.00", note: "Year 2026" },
    });
    expect(props.onChanged).toHaveBeenCalled();
    expect(props.onNotice).toHaveBeenCalled();
    expect(screen.queryByRole("form", { name: "Plan interest for 2026" })).not.toBeInTheDocument();
  });

  it("shows the server's message and keeps the form", async () => {
    apiMock.mockImplementation(() => Promise.reject(new Error("The amount must be positive")));
    const props = renderSection();
    fireEvent.click(screen.getByRole("button", { name: "Plan interest 2026" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The amount must be positive");
    expect(props.onLoan).not.toHaveBeenCalled();
    expect(screen.getByRole("form", { name: "Plan interest for 2026" })).toBeInTheDocument();
  });

  it("says when the loan is not available", () => {
    renderSection(null);
    expect(screen.getByText("The loan is not available.")).toBeInTheDocument();
  });
});
