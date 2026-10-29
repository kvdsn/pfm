import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { TopBar, type View } from "./TopBar";

const renderBar = (view: View) =>
  render(
    <TopBar
      accountType="personal"
      onAccountType={vi.fn()}
      years={["2025", "2026"]}
      year="2026"
      onYear={vi.fn()}
      onAccounts={vi.fn()}
      view={view}
      onTriage={vi.fn()}
      onRules={vi.fn()}
      onCategories={vi.fn()}
      onForecast={vi.fn()}
    />,
  );

describe("TopBar views", () => {
  afterEach(cleanup);

  it("lists the five views in order and marks the active one", () => {
    renderBar("rules");
    const buttons = within(screen.getByRole("navigation", { name: "Views" })).getAllByRole("button");
    expect(buttons.map((button) => button.textContent)).toEqual(["Triage", "Forecast", "Categories", "Rules", "Accounts"]);
    expect(buttons.map((button) => button.getAttribute("aria-pressed"))).toEqual(["false", "false", "false", "true", "false"]);
  });

  it("marks no view active on the overview", () => {
    renderBar("overview");
    const buttons = within(screen.getByRole("navigation", { name: "Views" })).getAllByRole("button");
    expect(buttons.every((button) => button.getAttribute("aria-pressed") === "false")).toBe(true);
  });
});

describe("TopBar year tabs", () => {
  afterEach(cleanup);

  it("marks a forecast year in its tooltip", () => {
    render(
      <TopBar
        accountType="personal" onAccountType={vi.fn()} years={["2026", "2027"]} year="2026" onYear={vi.fn()}
        forecastYears={["2027"]} onAccounts={vi.fn()} view="overview" onTriage={vi.fn()}
        onRules={vi.fn()} onCategories={vi.fn()} onForecast={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "2027" })).toHaveAttribute("title", "2027 · forecast");
    expect(screen.getByRole("button", { name: "2026" })).not.toHaveAttribute("title");
  });
});
