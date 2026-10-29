import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RulesView } from "./RulesView";
import type { ManagedCategory, RuleImpact } from "./types";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({ api: apiMock, errorMessage: (_: unknown, fallback: string) => fallback }));

const categoriesPayload = (): ManagedCategory[] => [
  {
    id: 1,
    name: "Groceries",
    transaction_count: 5,
    rules: [
      {
        id: 10,
        category_id: 1,
        match_text: "market",
        match_fields: ["merchant"],
        legacy_normalized: false,
        match_mode: "contains",
        flow_type: "expense",
        priority: 0,
        match_count: 3,
        first_match_count: 2,
        samples: [],
      },
    ],
  },
  { id: 2, name: "Uncategorized", transaction_count: 2, rules: [] },
  {
    id: 3,
    name: "Dining",
    transaction_count: 0,
    rules: [{
      id: 11,
      category_id: 3,
      match_text: "cafe",
      match_fields: ["merchant"],
      legacy_normalized: false,
      match_mode: "contains",
      flow_type: "expense",
      priority: 1,
      match_count: 1,
      first_match_count: 1,
      samples: [],
    }],
  },
];

const impact = (changeCount: number, releasedCount = 0, manual = false): RuleImpact => ({
  match_count: 1,
  win_count: 1,
  change_count: changeCount,
  field_counts: { merchant: 1, description: 4, memo: 0 },
  matches: [{
    id: 5,
    date: "2026-10-01",
    amount: "-12.50",
    merchant: "Supermarket Gent",
    description: "CARD PAYMENT SUPERMARKET",
    memo: "",
    category: "Groceries",
    decided_by: null,
    changes: changeCount > 0,
    manual,
  }],
  truncated: false,
  released: Array.from({ length: releasedCount }, () => ({
    id: 6,
    date: "2026-09-12",
    amount: "-4.20",
    merchant: "Market Bar",
    description: "",
    memo: "",
    category: "Groceries",
    new_category: "Dining",
    decided_by: "bar",
  })),
  released_count: releasedCount,
});

beforeEach(() => {
  apiMock.mockReset();
  let savedExact = false;
  type Options = { method?: string; json?: { category_id?: number; match_mode?: string; release_unmatched?: boolean } };
  apiMock.mockImplementation(async (url: string, options?: Options) => {
    if (url.includes("category-management")) return categoriesPayload();
    if (url.endsWith("/impact")) {
      return impact(options?.json?.category_id === 3 ? 1 : 0, options?.json?.match_mode === "exact" ? 1 : 0);
    }
    if (url.endsWith("/run")) return { updated: 1, released: savedExact ? 1 : 0 };
    if (url.endsWith("/order")) return { status: "updated" };
    if (url === "/api/category-rules") return { id: 12 };
    if (url.startsWith("/api/category-rules/")) {
      savedExact = options?.json?.match_mode === "exact";
      return { id: 10 };
    }
    throw new Error(`Unexpected API call: ${url}`);
  });
});

afterEach(cleanup);

const renderView = () => {
  const props = {
    accountType: "personal" as const,
    onNotice: vi.fn(),
    onError: vi.fn(),
    onChanged: vi.fn(async () => {}),
    onClose: vi.fn(),
  };
  render(<RulesView {...props} />);
  return props;
};

describe("RulesView", () => {
  it("previews a draft's impact and saves it only on request", async () => {
    const { onNotice, onChanged } = renderView();
    const merchant = await screen.findByText((_, element) =>
      element?.tagName === "DD" && element.textContent === "Supermarket Gent");
    expect(merchant.querySelector("mark")).toHaveTextContent("market");
    expect(screen.getByRole("button", { name: "Description 4" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Category of rule market"), { target: { value: "3" } });
    expect(await screen.findByText("1 would change")).toBeInTheDocument();
    expect(apiMock).not.toHaveBeenCalledWith("/api/category-rules/10", expect.anything());

    fireEvent.click(screen.getByRole("button", { name: "Save and apply" }));
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("Saved and applied “market” to 1 transaction."));
    expect(apiMock).toHaveBeenCalledWith("/api/category-rules/10", {
      method: "PUT",
      json: {
        account_type: "personal",
        category_id: 3,
        match_text: "market",
        match_fields: ["merchant"],
        match_mode: "contains",
        flow_type: "expense",
      },
    });
    expect(onChanged).toHaveBeenCalled();
  });

  it("lists the transactions an edit no longer decides and recategorizes them on apply", async () => {
    const { onNotice } = renderView();
    fireEvent.change(await screen.findByLabelText("Match mode of rule market"), { target: { value: "exact" } });
    expect(await screen.findByText("1 no longer decided")).toBeInTheDocument();
    expect(screen.getByText("No longer decided · 1")).toBeInTheDocument();
    expect(screen.getByText("Market Bar").closest(".impact-match")).toHaveTextContent("Groceries → Dining · rule “bar”");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("Rule saved."));
    expect(apiMock).toHaveBeenCalledWith("/api/category-rules/10", expect.objectContaining({
      json: expect.objectContaining({ match_mode: "exact" }),
    }));

    fireEvent.change(screen.getByLabelText("Match mode of rule market"), { target: { value: "exact" } });
    fireEvent.click(await screen.findByRole("button", { name: "Save and apply" }));
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith(
      "Saved and applied “market” to 1 transaction; recategorized 1 it no longer decides.",
    ));
  });

  it("offers contains, word and is as match modes", async () => {
    renderView();
    const select = await screen.findByLabelText("Match mode of rule market");
    expect(Array.from(select.querySelectorAll("option")).map((option) => option.textContent)).toEqual([
      "contains",
      "word",
      "is",
    ]);
  });

  it("marks only whole words in the impact pane for the word mode", async () => {
    renderView();
    const base = impact(0);
    apiMock.mockImplementation(async (url: string) => {
      if (url.includes("category-management")) return categoriesPayload();
      if (url.endsWith("/impact")) {
        return {
          ...base,
          matches: [
            { ...base.matches[0], id: 7, merchant: "Zara 392", description: "" },
            { ...base.matches[0], id: 8, merchant: "Zarakine", description: "" },
          ],
        };
      }
      throw new Error(`Unexpected API call: ${url}`);
    });
    fireEvent.change(await screen.findByLabelText("Text of rule market"), { target: { value: "Zara" } });
    fireEvent.change(screen.getByLabelText("Match mode of rule market"), { target: { value: "word" } });
    const whole = await screen.findByText((_, element) => element?.tagName === "DD" && element.textContent === "Zara 392");
    expect(whole.querySelector("mark")).toHaveTextContent("Zara");
    const partial = screen.getByText((_, element) => element?.tagName === "DD" && element.textContent === "Zarakine");
    expect(partial.querySelector("mark")).toBeNull();
  });

  it("reorders rules by dragging the handle onto another rule", async () => {
    renderView();
    const cafeRow = (await screen.findByLabelText("Text of rule cafe")).closest("tr")!;
    const marketRow = screen.getByLabelText("Text of rule market").closest("tr")!;
    const dataTransfer = { setData: vi.fn(), effectAllowed: "" };

    fireEvent.mouseDown(cafeRow.querySelector(".drag-handle")!);
    expect(cafeRow).toHaveAttribute("draggable", "true");
    fireEvent.dragStart(cafeRow, { dataTransfer });
    fireEvent.dragOver(marketRow, { dataTransfer, clientY: -1 });
    expect(marketRow).toHaveClass("drop-before");
    fireEvent.drop(marketRow, { dataTransfer });

    await waitFor(() => expect(apiMock).toHaveBeenCalledWith("/api/category-rules/order", {
      method: "PUT",
      json: { account_type: "personal", rule_ids: [11, 10] },
    }));
  });

  it("sorts by rule and category without changing priority, and drags only in priority order", async () => {
    renderView();
    const texts = () => screen.getAllByLabelText(/^Text of rule/).map((input) => (input as HTMLInputElement).value);
    expect(await screen.findByLabelText("Text of rule market")).toBeInTheDocument();
    expect(texts()).toEqual(["market", "cafe"]);

    fireEvent.click(screen.getByRole("button", { name: /^Rule/ }));
    expect(texts()).toEqual(["cafe", "market"]);
    expect(screen.getByLabelText("Text of rule cafe").closest("tr")!.querySelector(".rule-position")).toHaveTextContent("2");
    expect(document.querySelector(".drag-handle")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^Rule/ }));
    expect(texts()).toEqual(["market", "cafe"]);

    fireEvent.click(screen.getByRole("button", { name: /^Category/ }));
    expect(texts()).toEqual(["cafe", "market"]);
    fireEvent.click(screen.getByRole("button", { name: /^#/ }));
    expect(texts()).toEqual(["market", "cafe"]);
    expect(document.querySelectorAll(".drag-handle")).toHaveLength(2);
    expect(apiMock).not.toHaveBeenCalledWith("/api/category-rules/order", expect.anything());
  });

  it("starts with a new rule matching a search in every field", async () => {
    render(
      <RulesView
        accountType="personal"
        initialNewRuleText=" bakery "
        onNotice={vi.fn()}
        onError={vi.fn()}
        onChanged={vi.fn(async () => {})}
        onClose={vi.fn()}
      />,
    );
    expect(await screen.findByLabelText("Text of new rule")).toHaveValue("bakery");
    await waitFor(() => expect(apiMock).toHaveBeenCalledWith("/api/category-rules/impact", expect.objectContaining({
      json: expect.objectContaining({ match_text: "bakery", match_fields: ["merchant", "description", "memo"] }),
    })));
  });

  it("starts with the given rule selected", async () => {
    render(
      <RulesView
        accountType="personal"
        initialSelectedId={11}
        onNotice={vi.fn()}
        onError={vi.fn()}
        onChanged={vi.fn(async () => {})}
        onClose={vi.fn()}
      />,
    );
    await waitFor(() => expect(document.querySelector('[data-rule-id="11"]')).toHaveClass("selected-rule"));
    expect(document.querySelector('[data-rule-id="10"]')).not.toHaveClass("selected-rule");
    await waitFor(() => expect(apiMock).toHaveBeenCalledWith("/api/category-rules/impact", expect.objectContaining({
      json: expect.objectContaining({ match_text: "cafe" }),
    })));
  });

  it("starts a new rule when the new item signal changes", async () => {
    const props = { accountType: "personal" as const, onNotice: vi.fn(), onError: vi.fn(), onChanged: vi.fn(async () => {}), onClose: vi.fn() };
    const { rerender } = render(<RulesView {...props} newItemSignal={0} />);
    await screen.findByLabelText("Text of rule market");
    expect(screen.queryByLabelText("Text of new rule")).not.toBeInTheDocument();
    rerender(<RulesView {...props} newItemSignal={1} />);
    expect(await screen.findByLabelText("Text of new rule")).toBeInTheDocument();
    rerender(<RulesView {...props} newItemSignal={2} />);
    expect(screen.getAllByLabelText("Text of new rule")).toHaveLength(1);
  });

  it("adds a new rule at the end, previews it, and saves it", async () => {
    const { onNotice } = renderView();
    fireEvent.click(await screen.findByRole("button", { name: "New rule" }));
    const text = screen.getByLabelText("Text of new rule");
    expect(text).toHaveFocus();
    expect(screen.getByRole("button", { name: "New rule" })).toBeDisabled();
    fireEvent.change(text, { target: { value: "bakery" } });
    fireEvent.change(screen.getByLabelText("Category of new rule"), { target: { value: "3" } });

    await waitFor(() => expect(apiMock).toHaveBeenCalledWith("/api/category-rules/impact", {
      method: "POST",
      json: {
        account_type: "personal",
        category_id: 3,
        match_text: "bakery",
        match_fields: ["merchant"],
        match_mode: "contains",
        flow_type: "expense",
      },
    }));
    fireEvent.click(screen.getByRole("button", { name: "Save and apply" }));
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("Saved and applied “bakery” to 1 transaction."));
    expect(apiMock).toHaveBeenCalledWith("/api/category-rules", {
      method: "POST",
      json: {
        account_type: "personal",
        category_id: 3,
        match_text: "bakery",
        match_fields: ["merchant"],
        match_mode: "contains",
        flow_type: "expense",
      },
    });
    expect(apiMock).toHaveBeenCalledWith("/api/category-rules/12/run", { method: "POST", json: { account_type: "personal" } });
  });

  it("offers to apply an unedited rule that has transactions to release, and shows hand-set matches", async () => {
    const base = apiMock.getMockImplementation()!;
    apiMock.mockImplementation(async (url: string, options?: unknown) =>
      url.endsWith("/impact") ? impact(2, 1, true) : base(url, options));
    renderView();
    expect(await screen.findByRole("button", { name: "Apply to 3 transactions" })).toBeInTheDocument();
    const card = (await screen.findByText((_, element) =>
      element?.tagName === "DD" && element.textContent === "Supermarket Gent")).closest(".impact-match");
    expect(card).toHaveTextContent("Set by hand · stays Groceries");
    expect(card).toHaveClass("claimed");
  });

  it("discards a draft", async () => {
    renderView();
    const text = await screen.findByLabelText("Text of rule market");
    fireEvent.change(text, { target: { value: "shop" } });
    fireEvent.click(await screen.findByRole("button", { name: "Discard" }));
    expect(screen.getByLabelText("Text of rule market")).toHaveValue("market");
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  });
  it("moves the selected rule with the up and down arrows", async () => {
    renderView();
    const selectedText = () => document.querySelector(".selected-rule .rule-text-cell input");
    await screen.findByLabelText("Text of rule cafe");
    expect(selectedText()).toHaveValue("market");

    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(selectedText()).toHaveValue("cafe");
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(selectedText()).toHaveValue("cafe");
    fireEvent.keyDown(window, { key: "ArrowUp" });
    expect(selectedText()).toHaveValue("market");

    fireEvent.keyDown(screen.getByLabelText("Filter rules"), { key: "ArrowDown" });
    expect(selectedText()).toHaveValue("market");
  });

  describe("rule health", () => {
    const rule = (id: number, text: string, extra: Partial<ManagedCategory["rules"][number]>) => ({
      id,
      category_id: 1,
      match_text: text,
      match_fields: ["merchant" as const],
      legacy_normalized: false,
      match_mode: "contains" as const,
      flow_type: "expense" as const,
      priority: id,
      match_count: 3,
      first_match_count: 3,
      samples: [],
      ...extra,
    });
    const healthPayload = (): ManagedCategory[] => [
      {
        id: 1,
        name: "Groceries",
        transaction_count: 6,
        rules: [
          rule(10, "market", { health: [] }),
          rule(11, "mark", {
            first_match_count: 0,
            health: ["idle", "shadowed", "broad"],
            shadowed_by: [{ id: 10, match_text: "market", count: 3 }],
          }),
          rule(12, "shop", { health: [] }),
          rule(13, "gone", { match_count: 0, first_match_count: 0, health: ["idle"] }),
        ],
      },
    ];
    const renderHealth = (payload: ManagedCategory[] = healthPayload()) => {
      const base = apiMock.getMockImplementation()!;
      apiMock.mockImplementation(async (url: string, options?: unknown) =>
        url.includes("category-management") ? payload : base(url, options));
      renderView();
    };
    const visibleRules = () => Array.from(document.querySelectorAll<HTMLInputElement>(".rule-text-cell input")).map((input) => input.value);

    it("filters by health, with counts in the options, and combines with the text filter", async () => {
      renderHealth();
      await screen.findByLabelText("Text of rule shop");
      const options = Array.from(screen.getByLabelText("Filter by health").querySelectorAll("option")).map((option) => option.textContent);
      expect(options).toEqual([
        "All rules", "Needs attention (2)", "Idle (2)", "Shadowed (1)", "Broad (1)",
      ]);
      expect(visibleRules()).toEqual(["market", "mark", "shop", "gone"]);

      fireEvent.change(screen.getByLabelText("Filter by health"), { target: { value: "attention" } });
      expect(visibleRules()).toEqual(["mark", "gone"]);
      fireEvent.change(screen.getByLabelText("Filter by health"), { target: { value: "idle" } });
      expect(visibleRules()).toEqual(["mark", "gone"]);
      fireEvent.change(screen.getByLabelText("Filter rules"), { target: { value: "gon" } });
      expect(visibleRules()).toEqual(["gone"]);
      fireEvent.change(screen.getByLabelText("Filter rules"), { target: { value: "" } });
      fireEvent.change(screen.getByLabelText("Filter by health"), { target: { value: "shadowed" } });
      expect(visibleRules()).toEqual(["mark"]);
      fireEvent.change(screen.getByLabelText("Filter by health"), { target: { value: "broad" } });
      expect(visibleRules()).toEqual(["mark"]);
    });

    it("sorts the filtered rules", async () => {
      renderHealth();
      await screen.findByLabelText("Text of rule shop");
      fireEvent.change(screen.getByLabelText("Filter by health"), { target: { value: "attention" } });
      fireEvent.click(screen.getByRole("button", { name: /^Rule/ }));
      expect(visibleRules()).toEqual(["gone", "mark"]);
    });

    it("tags flagged rows with a tooltip", async () => {
      renderHealth();
      await screen.findByLabelText("Text of rule shop");
      const rows = Array.from(document.querySelectorAll("tbody tr"));
      const tags = (index: number) => Array.from(rows[index].querySelectorAll(".health-tag")).map((tag) => tag.textContent);
      expect(tags(0)).toEqual([]);
      expect(tags(1)).toEqual(["idle", "shadowed", "broad"]);
      expect(tags(2)).toEqual([]);
      expect(tags(3)).toEqual(["idle"]);
      expect(rows[1].querySelectorAll(".health-tag")[1]).toHaveAttribute(
        "title", "Matches 3 transactions that rule #1 “market” decides first.");
      expect(rows[3].querySelector(".health-tag")).toHaveAttribute("title", "Matches no transactions.");
    });

    it("lists a rule's signals in the Impact pane and selects an earlier rule from them", async () => {
      renderHealth();
      await screen.findByLabelText("Text of rule shop");
      const selectedText = () => document.querySelector(".selected-rule .rule-text-cell input");
      expect(selectedText()).toHaveValue("market");
      expect(screen.queryByRole("region", { name: "Rule health" })).not.toBeInTheDocument();

      fireEvent.click(screen.getByLabelText("Text of rule mark"));
      const health = screen.getByRole("region", { name: "Rule health" });
      expect(health).toHaveTextContent("Matches 3 transactions that earlier rules decide first: #1 “market” (3).");
      expect(health).toHaveTextContent("it may match more than intended");

      fireEvent.click(within(health).getByRole("button", { name: "#1" }));
      expect(selectedText()).toHaveValue("market");
      expect(screen.queryByRole("region", { name: "Rule health" })).not.toBeInTheDocument();
    });

    it("treats a missing health field as no signals", async () => {
      const payload = healthPayload();
      payload[0].rules.forEach((item) => { delete item.health; delete item.shadowed_by; });
      renderHealth(payload);
      await screen.findByLabelText("Text of rule shop");
      expect(document.querySelectorAll(".health-tag")).toHaveLength(0);
      expect(screen.queryByRole("region", { name: "Rule health" })).not.toBeInTheDocument();
      expect(screen.getByRole("option", { name: "Needs attention (0)" })).toBeInTheDocument();
    });
  });

  it("shows how many of a rule's matches are itemized card settlements", async () => {
    const base = apiMock.getMockImplementation()!;
    apiMock.mockImplementation(async (url: string, options?: unknown) => {
      if (url.includes("category-management")) {
        const data = categoriesPayload();
        data[0].rules[0].itemized_count = 3;
        return data;
      }
      return base(url, options);
    });
    renderView();
    // A neutral tag beside the rule's health tags, not part of the counts.
    const tag = await screen.findByText("3 itemized");
    expect(tag).toHaveClass("health-tag");
    expect(tag.getAttribute("title")).toMatch(/^3 of \d+ matches are card payments replaced by their statement's purchases/);
    expect(screen.getAllByText(/itemized/)).toHaveLength(1);
  });
});
