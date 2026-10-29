import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ShortcutsDialog } from "./ShortcutsDialog";
import { Footer } from "./TopBar";

afterEach(cleanup);

describe("Keyboard shortcuts dialog", () => {
  it("groups the shortcuts under subheadings in kbd style", () => {
    render(<ShortcutsDialog onClose={vi.fn()} />);
    const dialog = screen.getByRole("dialog", { name: "Keyboard shortcuts" });
    expect(within(dialog).getAllByRole("heading", { level: 4 }).map((heading) => heading.textContent))
      .toEqual(["Anywhere", "Overview", "Triage view", "Forecast view", "Rules view", "Categories view", "Accounts view"]);
    const anywhere = within(screen.getByRole("region", { name: "Anywhere" }));
    expect(anywhere.getByText("personal, business, or kids")).toBeInTheDocument();
    expect(anywhere.getByText("accounts, imports and backups").previousElementSibling?.textContent).toBe("A");
    expect(anywhere.getByText("import files").previousElementSibling?.textContent).toBe("I");
    expect(within(screen.getByRole("region", { name: "Accounts view" })).getByText("move through accounts, or after a click in backups, through backups")).toBeInTheDocument();
    expect(anywhere.getByText("keyboard shortcuts")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Overview" })).getByText("search")).toBeInTheDocument();
    const overview = within(screen.getByRole("region", { name: "Overview" }));
    expect(overview.getByText("next uncategorized transaction")).toBeInTheDocument();
    expect(overview.getByText("open the selected transaction's rule")).toBeInTheDocument();
    expect(overview.getByText("check or uncheck the active row or range").previousElementSibling?.textContent).toBe("X");
    expect(overview.getByText("extend the range").previousElementSibling?.textContent).toBe("Shift+↑/↓");
    expect(overview.getByText("set the category of the selected transaction, or of the ticked ones")).toBeInTheDocument();
    expect(overview.getByText("in the bulk bar, choose, then apply").previousElementSibling?.textContent).toBe("Enter");
    expect(overview.getByText("search").previousElementSibling?.textContent).toBe("/");
    expect(within(screen.getByRole("region", { name: "Rules view" })).getByText("move through rules")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Rules view" })).getByText("new rule")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Categories view" })).getByText("new category")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Accounts view" })).getByText("import files, or after a click in backups, a new backup note")).toBeInTheDocument();
    expect(dialog.querySelectorAll("kbd").length).toBeGreaterThan(10);
    expect(anywhere.getByText("forecast: the plan, recurring payments and their inputs").previousElementSibling?.textContent).toBe("F");
    expect(within(screen.getByRole("region", { name: "Forecast view" })).getByText("add a planned item, or after a click in recurring payments, add a payment").previousElementSibling?.textContent).toBe("N");
    expect(within(screen.getByRole("region", { name: "Overview" })).getByText("show follow-ups, or all transactions").previousElementSibling?.textContent).toBe("Shift+M");
    expect(within(dialog).queryByText("Z")).not.toBeInTheDocument();
  });

  it("closes with its close button", () => {
    const onClose = vi.fn();
    render(<ShortcutsDialog onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "Close keyboard shortcuts" }));
    expect(onClose).toHaveBeenCalled();
  });
});

describe("Footer", () => {
  it("shows only a single hint for the shortcuts dialog", () => {
    const { container } = render(<Footer />);
    expect(container.querySelectorAll("kbd")).toHaveLength(1);
    expect(screen.getByText("keyboard shortcuts", { exact: false }).textContent).toBe("K keyboard shortcuts");
    expect(screen.getByText(/Stored locally on this computer/)).toBeInTheDocument();
  });
});
