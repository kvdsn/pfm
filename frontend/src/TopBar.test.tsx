import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { TopBar, type View } from "./TopBar";

const renderBar = (view: View, onOverview = vi.fn()) =>
  render(
    <TopBar
      accountType="personal"
      onAccountType={vi.fn()}
      years={["2025", "2026"]}
      year="2026"
      onYear={vi.fn()}
      onAccounts={vi.fn()}
      view={view}
      onOverview={onOverview}
      onTriage={vi.fn()}
      onRules={vi.fn()}
      onCategories={vi.fn()}
      onForecast={vi.fn()}
    />,
  );

describe("TopBar views", () => {
  afterEach(cleanup);

  it("lists Overview and the five views in order and marks the active one", () => {
    renderBar("rules");
    const buttons = within(screen.getByRole("navigation", { name: "Views" })).getAllByRole("button");
    expect(buttons.map((button) => button.textContent)).toEqual(["Overview", "Triage", "Forecast", "Categories", "Rules", "Accounts"]);
    expect(buttons.map((button) => button.getAttribute("aria-pressed"))).toEqual(["false", "false", "false", "false", "true", "false"]);
  });

  it("marks only Overview active on the overview, and Overview returns to it", () => {
    const onOverview = vi.fn();
    renderBar("rules", onOverview);
    fireEvent.click(screen.getByRole("button", { name: "Overview" }));
    expect(onOverview).toHaveBeenCalledTimes(1);
    cleanup();
    renderBar("overview");
    const buttons = within(screen.getByRole("navigation", { name: "Views" })).getAllByRole("button");
    expect(buttons.map((button) => button.getAttribute("aria-pressed"))).toEqual(["true", "false", "false", "false", "false", "false"]);
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
