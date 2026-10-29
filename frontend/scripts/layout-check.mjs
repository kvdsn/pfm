import puppeteer from "puppeteer-core";
import { mkdirSync } from "node:fs";

const BROWSER = "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi";
const APP_URL = process.env.PFM_URL || "http://127.0.0.1:8000";
const SCREENSHOTS = new URL("../layout-screenshots/", import.meta.url).pathname;
mkdirSync(SCREENSHOTS, { recursive: true });

const failures = [];
const check = (label, condition, detail = "") => {
  if (condition) {
    console.log(`ok   ${label}`);
  } else {
    failures.push(label);
    console.log(`FAIL ${label}${detail ? ` (${detail})` : ""}`);
  }
};

const browser = await puppeteer.launch({
  executablePath: BROWSER,
  headless: true,
  args: ["--no-first-run", "--disable-gpu", "--window-size=1728,1000"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1728, height: 1000 });
const logs = [];
page.on("console", (message) => logs.push(`${message.type()}: ${message.text()}`));
page.on("pageerror", (error) => logs.push(`pageerror: ${error.message}`));
await page.goto(APP_URL, { waitUntil: "networkidle0", timeout: 20000 });
if (await page.$(".year-summary-table tbody tr") === null) {
  await page.click("button.account-tab:nth-child(2)");
}
await page.waitForSelector(".year-summary-table tbody tr", { timeout: 10000 });

const shots = [];
const shoot = async (name) => {
  await page.screenshot({ path: `${SCREENSHOTS}${name}.png`, fullPage: false });
  shots.push(name);
};

check(
  "footer shows only the keyboard shortcuts hint, not the full legend",
  await page.evaluate(() => {
    const footer = document.querySelector("footer");
    return footer.querySelectorAll("kbd").length === 1 && footer.textContent.includes("K keyboard shortcuts");
  }),
);

const geometry = await page.evaluate(() => {
  const rect = (element) => {
    const box = element.getBoundingClientRect();
    return { x: box.x, y: box.y, width: box.width, height: box.height, right: box.right, bottom: box.bottom };
  };
  const table = document.querySelector(".year-summary-table");
  const headerCells = [...document.querySelectorAll(".year-summary-table thead th")].map(rect);
  const rows = [...document.querySelectorAll(".year-summary-table tbody tr")].map((row) => ({
    className: row.className,
    cells: [...row.children].map(rect),
  }));
  const firstCategoryCell = document.querySelector(".year-summary-table .year-category-row th");
  const name = firstCategoryCell?.querySelector(".category-name-static");
  const count = firstCategoryCell?.querySelector(".category-row-count");
  return {
    table: table ? rect(table) : null,
    headerCells,
    rows,
    name: name ? rect(name) : null,
    count: count ? rect(count) : null,
    docWidth: document.documentElement.scrollWidth,
    windowWidth: window.innerWidth,
  };
});

check("year table rendered with data", geometry.table !== null);
check(
  "all header columns have width (16, plus the year-over-year and Left columns when shown)",
  [16, 17, 18].includes(geometry.headerCells.length) && geometry.headerCells.every((cell) => cell.width > 20),
  `${geometry.headerCells.length} columns, widths ${geometry.headerCells.map((c) => Math.round(c.width)).join(",")}`,
);
check(
  "category name and count share one line",
  geometry.name && geometry.count && Math.abs(geometry.name.y - geometry.count.y) < 2,
  `name y=${geometry.name?.y}, count y=${geometry.count?.y}`,
);
check(
  "count aligns right inside the category cell",
  geometry.count && geometry.table && geometry.count.right <= geometry.name.x + geometry.name.width + 400
    && geometry.count.x > geometry.name.x + geometry.name.width * 0.5,
);
check(
  "no horizontal document overflow",
  geometry.docWidth <= geometry.windowWidth + 1,
  `doc ${geometry.docWidth} vs viewport ${geometry.windowWidth}`,
);
await shoot("01-overview");

// Forecast: a future month of the current year shows projected amounts, in italics. There is nothing to project
// in December, or on a server without /api/forecast.
{
  const projected = await page.evaluate(() => {
    const futureHeaders = document.querySelectorAll(".year-summary-table thead th.future-month").length;
    const cells = [...document.querySelectorAll(".year-summary-table tbody td.projected")];
    return {
      futureHeaders,
      cells: cells.length,
      filled: cells.filter((cell) => /\d/.test(cell.textContent)).length,
      italic: cells.length > 0 && cells.every((cell) => getComputedStyle(cell).fontStyle === "italic"),
    };
  });
  if (projected.futureHeaders > 0) {
    check("a future month of the year table shows projected amounts", projected.filled > 0, JSON.stringify(projected));
    check("projected cells are italic", projected.italic, JSON.stringify(projected));
  }
}

/**
 * Scrolls so the table's top edge is `offset` px above the viewport top; returns where its head, its column
 * headings (the head's last row, under the transactions table's action row) and its bottom ended up.
 */
const scrollPastTableTop = async (tableSelector, offset) => {
  await page.evaluate(({ selector, above }) => {
    const top = document.querySelector(selector).getBoundingClientRect().top;
    window.scrollBy(0, top + above);
  }, { selector: tableSelector, above: offset });
  await new Promise((resolve) => setTimeout(resolve, 200));
  return page.evaluate((selector) => {
    const table = document.querySelector(selector);
    const tops = [...table.querySelectorAll("thead tr:last-child th")].map((cell) => cell.getBoundingClientRect().top);
    const header = table.querySelector("thead").getBoundingClientRect();
    return {
      headTop: header.top,
      tableTop: table.getBoundingClientRect().top,
      tableBottom: table.getBoundingClientRect().bottom,
      headerHeight: header.height,
      minTop: Math.min(...tops),
      maxTop: Math.max(...tops),
    };
  }, tableSelector);
};
const yearScrolled = await scrollPastTableTop(".year-summary-table", 150);
check(
  "year table header stays at the top of the viewport while scrolling",
  yearScrolled.tableTop < -100 && yearScrolled.tableBottom > yearScrolled.headerHeight + 40
    && Math.abs(yearScrolled.minTop) <= 3 && Math.abs(yearScrolled.maxTop) <= 3,
  JSON.stringify(yearScrolled),
);
await shoot("07-sticky-year-header");
if (await page.$(".transactions-table thead th")) {
  const txScrolled = await scrollPastTableTop(".transactions-table", 100);
  check(
    "transactions table header stays at the top of the viewport while scrolling",
    txScrolled.tableTop < -50 && txScrolled.tableBottom > txScrolled.headerHeight + 40
      && Math.abs(txScrolled.headTop) <= 3 && txScrolled.minTop >= 0 && txScrolled.maxTop - txScrolled.minTop <= 1
      && txScrolled.maxTop < txScrolled.headerHeight,
    JSON.stringify(txScrolled),
  );
  await shoot("08-sticky-transactions-header");
} else {
  check("transactions table present for the sticky header check", false);
}
await page.evaluate(() => window.scrollTo(0, 0));

await page.click(".year-summary-table .year-category-row th");
await new Promise((resolve) => setTimeout(resolve, 500));
const rowClick = await page.evaluate(() => ({
  filter: Boolean(document.querySelector(".transactions-panel .active-filter")),
  pane: Boolean(document.querySelector(".category-edit-pane")),
}));
check("category row filters transactions without an edit pane", rowClick.filter && !rowClick.pane);

await page.keyboard.press("k");
await page.waitForSelector(".shortcuts-dialog", { timeout: 5000 });
const shortcuts = await page.evaluate(() => {
  const box = document.querySelector(".shortcuts-dialog").getBoundingClientRect();
  return {
    fits: box.top >= 0 && box.left >= 0 && box.bottom <= window.innerHeight && box.right <= window.innerWidth,
    groups: [...document.querySelectorAll(".shortcut-group h4")].map((heading) => heading.textContent),
  };
});
check("shortcuts dialog opens with K and fits the viewport", shortcuts.fits);
check("shortcuts dialog lists its groups", shortcuts.groups.join(",") === "Anywhere,Overview,Triage view,Forecast view,Rules view,Categories view,Accounts view", shortcuts.groups.join(","));
await shoot("06-shortcuts");
await page.keyboard.press("k");
check("K closes the shortcuts dialog", await page.$(".shortcuts-dialog") === null);

// Top bar: title, views, entity switch and year group must never overlap, at any width, and the page must not scroll sideways.
{
  const measureTopbar = () => page.evaluate(() => {
    const box = (selector) => {
      const element = document.querySelector(selector);
      if (!element) return null;
      const r = element.getBoundingClientRect();
      return { left: r.left, right: r.right, top: r.top, bottom: r.bottom };
    };
    return {
      title: box(".topbar .brand h1"),
      views: box(".topbar-actions"),
      entity: box(".topbar-controls > .account-tabs:not(.year-tabs)"),
      years: box(".year-tabs"),
      docWidth: document.documentElement.scrollWidth,
      bar: box(".topbar"),
    };
  });
  const overlaps = (a, b) => a && b && a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5;
  const original = page.viewport();
  for (const width of [1728, 1100, 1024, 850, 700, 480]) {
    await page.setViewport({ ...original, width });
    await new Promise((resolve) => setTimeout(resolve, 150));
    const t = await measureTopbar();
    const parts = { title: t.title, views: t.views, entity: t.entity, years: t.years };
    const names = Object.keys(parts);
    const clashes = [];
    for (let i = 0; i < names.length; i++) for (let j = i + 1; j < names.length; j++) if (overlaps(parts[names[i]], parts[names[j]])) clashes.push(`${names[i]}/${names[j]}`);
    check(`top bar parts do not overlap at ${width}px`, clashes.length === 0 && t.entity && t.views && t.title, clashes.join(",") + " " + JSON.stringify(parts));
    check(`top bar does not overflow horizontally at ${width}px`, t.docWidth <= width + 1 && t.views.right <= width + 1 && t.views.left >= -1, `doc ${t.docWidth}`);
    if (width === 1728) {
      // The main display width: title, switches and views on one row, the view buttons on one line.
      const buttonRows = await page.evaluate(() => new Set([...document.querySelectorAll(".topbar-actions button")].map((button) => Math.round(button.getBoundingClientRect().top))).size);
      check("top bar is one row at 1728px", Math.abs(t.title.top - t.views.top) < 16 && Math.abs(t.entity.top - t.views.top) < 16 && buttonRows === 1, `button rows ${buttonRows}`);
    }
    if ([1728, 1024, 850, 480].includes(width)) {
      const name = `00-topbar-${width}`;
      await page.screenshot({ path: `${SCREENSHOTS}${name}.png`, clip: { x: 0, y: 0, width: await page.evaluate(() => document.documentElement.clientWidth), height: Math.ceil(t.bar.bottom + 16) } });
      shots.push(name);
    }
  }
  await page.setViewport(original);
}

/** Opens a full-page view from the top bar and measures its two panes. */
const openView = async (label, listSelector) => {
  await page.evaluate((text) => {
    [...document.querySelectorAll(".topbar-actions button")].find((button) => button.textContent.trim() === text).click();
  }, label);
  await page.waitForSelector(`${listSelector} tbody tr`, { timeout: 10000 });
  await new Promise((resolve) => setTimeout(resolve, 600));
  return page.evaluate((selector) => {
    const list = document.querySelector(".rules-pane").getBoundingClientRect();
    const side = document.querySelector(".impact-pane")?.getBoundingClientRect();
    const scroller = document.querySelector(".rules-pane .table-scroll");
    return {
      overviewHidden: !document.querySelector(".year-overview"),
      stacked: Boolean(side) && side.top >= list.bottom - 1 && Math.abs(side.left - list.left) < 2,
      active: [...document.querySelectorAll(".topbar-actions button.active")].map((button) => button.textContent.trim()),
      sideBySide: Boolean(side) && side.left >= list.right - 1 && Math.abs(side.top - list.top) < 2,
      tableFits: scroller.scrollWidth <= scroller.clientWidth + 1,
      rows: document.querySelectorAll(`${selector} tbody tr`).length,
      docWidth: document.documentElement.scrollWidth,
    };
  }, listSelector);
};

const rules = await openView("Rules", ".rules-table");
check("rules view replaces the overview", rules.overviewHidden);
check("rules list and impact pane sit side by side", rules.sideBySide);
check("rules table fits without horizontal scrolling", rules.tableFits);
await shoot("02-rules");

const categories = await openView("Categories", ".categories-table");
check("categories view lists categories beside their details", categories.sideBySide && categories.rows > 1);
check("categories table fits without horizontal scrolling", categories.tableFits);
check("full-page views do not overflow", Math.max(rules.docWidth, categories.docWidth) <= 1728 + 1);
await shoot("03-categories");
await page.keyboard.press("Escape");

/** Opens a single-pane view (imports, backups) from the top bar and checks it fits the page. */
const openSinglePane = async (label, tableSelector) => {
  await page.evaluate((text) => {
    [...document.querySelectorAll(".topbar-actions button")].find((button) => button.textContent.trim() === text).click();
  }, label);
  await page.waitForSelector(`${tableSelector} thead`, { timeout: 10000 });
  await new Promise((resolve) => setTimeout(resolve, 600));
  return page.evaluate((selector) => {
    const scroller = document.querySelector(".rules-pane .table-scroll");
    return {
      overviewHidden: !document.querySelector(".year-overview"),
      active: [...document.querySelectorAll(".topbar-actions button.active")].map((button) => button.textContent.trim()),
      tableFits: scroller.scrollWidth <= scroller.clientWidth + 1,
      docWidth: document.documentElement.scrollWidth,
    };
  }, tableSelector);
};

for (const [label, selector, shot] of [["Triage", ".triage-table", "09-triage"]]) {
  const single = await openSinglePane(label, selector);
  check(`${label} view replaces the overview`, single.overviewHidden);
  check(`${label} top-bar button is marked active`, single.active.length === 1 && single.active[0] === label, single.active.join(","));
  check(`${label} table fits without horizontal scrolling`, single.tableFits);
  check(`${label} view does not overflow the page`, single.docWidth <= 1728 + 1, `doc ${single.docWidth}`);
  await shoot(shot);
  await page.keyboard.press("Escape");
}

// Accounts: one view with the accounts table and the selected account's details. The live server may predate the
// imports in /api/accounts/overview; the details pane then says "No imports." and the layout is the same.
const accountsWide = await openView("Accounts", ".accounts-table");
check("Accounts view replaces the overview", accountsWide.overviewHidden);
check("Accounts top-bar button is marked active", accountsWide.active.length === 1 && accountsWide.active[0] === "Accounts", accountsWide.active.join(","));
check("Imports has no top-bar button", await page.evaluate(() => ![...document.querySelectorAll(".topbar-actions button")].some((button) => button.textContent.trim() === "Imports")));
check("Accounts table and details pane sit side by side at 1728px", accountsWide.sideBySide);
check("Accounts table fits without horizontal scrolling", accountsWide.tableFits);
check("Accounts view does not overflow the page", accountsWide.docWidth <= 1728 + 1, `doc ${accountsWide.docWidth}`);
check("Accounts heading and Import files button render", await page.evaluate(() => document.querySelector(".rules-pane h3")?.textContent.startsWith("Accounts and cards") && document.querySelector(".rules-pane .choose-file")?.textContent.trim() === "Import files"));
await shoot("04-accounts");
// The backups sit under the accounts on the same page; there is no Backups button.
const backups = await page.evaluate(() => {
  const table = document.querySelector('[aria-label="Backups"] .backups-table');
  const accounts = document.querySelector('[aria-label="Accounts"]');
  const scroller = table?.closest(".table-scroll");
  table?.scrollIntoView();
  return {
    below: Boolean(table && accounts) && table.getBoundingClientRect().top >= accounts.getBoundingClientRect().bottom - 1,
    fits: Boolean(scroller) && scroller.scrollWidth <= scroller.clientWidth + 1,
    noButton: ![...document.querySelectorAll(".topbar-actions button")].some((button) => ["Backups", "Recurring", "Plan"].includes(button.textContent.trim())),
  };
});
check("Backups sit under the accounts on the Accounts page", backups.below);
check("Backups table fits without horizontal scrolling", backups.fits);
check("Backups, Recurring and Plan have no top-bar button", backups.noButton);
await new Promise((resolve) => setTimeout(resolve, 300));
await shoot("05-backups");
await page.keyboard.press("Escape");

// Recurring: the table and, once a row is selected, the details pane. The live server may predate /api/recurring;
// the check is skipped then, because the view cannot load.
{
  const openRecurring = async () => {
    await page.evaluate(() => {
      [...document.querySelectorAll(".topbar-actions button")].find((button) => button.textContent.trim() === "Forecast").click();
    });
    await page.waitForSelector(".recurring-summary", { timeout: 10000 }).catch(() => null);
    const row = await page.$(".recurring-table tbody tr");
    if (row) await row.click();
    await new Promise((resolve) => setTimeout(resolve, 600));
    return page.evaluate(() => {
      const scrollers = [...document.querySelectorAll(".rules-pane .table-scroll")];
      return {
        loaded: Boolean(document.querySelector(".recurring-summary")),
        rows: document.querySelectorAll(".recurring-table tbody tr").length,
        paneShown: Boolean(document.querySelector(".recurring-pane")),
        tablesFit: scrollers.every((scroller) => scroller.scrollWidth <= scroller.clientWidth + 1),
        docWidth: document.documentElement.scrollWidth,
      };
    });
  };
  for (const [width, shot] of [[1728, "10-recurring"], [1024, "10-recurring-1024"], [850, "10-recurring-850"]]) {
    await page.setViewport({ width, height: 1000 });
    const recurring = await openRecurring();
    if (!recurring.loaded) {
      check(`Recurring view loads at ${width}px (the server may need restarting)`, false);
    } else {
      check(`Recurring view tables fit without horizontal scrolling at ${width}px`, recurring.tablesFit);
      check(`Recurring view does not overflow the page at ${width}px`, recurring.docWidth <= width + 1, `doc ${recurring.docWidth}`);
      if (recurring.rows > 0) check(`Recurring details pane shows for a selected row at ${width}px`, recurring.paneShown);
    }
    await shoot(shot);
    await page.keyboard.press("Escape");
  }
  await page.setViewport({ width: 850, height: 1000 });
}

// Plan: stacked panels with compact tables. The live server may predate /api/forecast; the check is skipped then,
// because the view cannot load. It only reads: nothing is added, changed or deleted.
{
  const openPlan = async () => {
    await page.evaluate(() => {
      [...document.querySelectorAll(".topbar-actions button")].find((button) => button.textContent.trim() === "Forecast").click();
    });
    await page.waitForSelector('.plan-view [aria-label="Baselines"]', { timeout: 10000 }).catch(() => null);
    await new Promise((resolve) => setTimeout(resolve, 400));
    return page.evaluate(() => ({
      loaded: Boolean(document.querySelector('.plan-view [aria-label="Baselines"]')),
      panels: document.querySelectorAll(".plan-view .plan-panel").length,
      tablesFit: [...document.querySelectorAll(".plan-view .table-scroll")].every((scroller) => scroller.scrollWidth <= scroller.clientWidth + 1),
      panelsFit: [...document.querySelectorAll(".plan-view .plan-panel")].every((panel) => panel.scrollWidth <= panel.clientWidth + 1),
      overviewHidden: !document.querySelector(".year-overview"),
      docWidth: document.documentElement.scrollWidth,
    }));
  };
  for (const [width, shot] of [[1728, "11-plan"], [1024, "11-plan-1024"], [850, "11-plan-850"]]) {
    await page.setViewport({ width, height: 1000 });
    const plan = await openPlan();
    if (!plan.loaded) {
      check(`Plan view loads at ${width}px (the server may need restarting)`, false);
    } else {
      check(`Plan view replaces the overview at ${width}px`, plan.overviewHidden && plan.panels >= 4);
      check(`Plan view tables and panels fit without horizontal scrolling at ${width}px`, plan.tablesFit && plan.panelsFit);
      check(`Plan view does not overflow the page at ${width}px`, plan.docWidth <= width + 1, `doc ${plan.docWidth}`);
    }
    await shoot(shot);
    await page.keyboard.press("Escape");
  }
  await page.setViewport({ width: 850, height: 1000 });
}

// The app's browser pane is often about 850px wide: the triage table must still fit there.
await page.setViewport({ width: 850, height: 1000 });
const narrowTriage = await openSinglePane("Triage", ".triage-table");
check("Triage table fits at 850px", narrowTriage.tableFits && narrowTriage.docWidth <= 850 + 1, `doc ${narrowTriage.docWidth}`);
await shoot("09-triage-850");
await page.keyboard.press("Escape");
const narrowAccounts = await openView("Accounts", ".accounts-table");
check("Accounts table fits at 850px", narrowAccounts.tableFits && narrowAccounts.docWidth <= 850 + 1, `doc ${narrowAccounts.docWidth}`);
check("Accounts details pane stacks under the table at 850px", narrowAccounts.stacked);
await shoot("04-accounts-850");
await page.keyboard.press("Escape");
await page.setViewport({ width: 1024, height: 1000 });
const midAccounts = await openView("Accounts", ".accounts-table");
check("Accounts view fits at 1024px", midAccounts.tableFits && midAccounts.docWidth <= 1024 + 1, `doc ${midAccounts.docWidth}`);
await page.setViewport({ width: 850, height: 1000 });
await page.keyboard.press("Escape");
const narrowRules = await openView("Rules", ".rules-table");
check("Rules view does not overflow the page at 850px", narrowRules.docWidth <= 850 + 1, `doc ${narrowRules.docWidth}`);
check("rules table fits without horizontal scrolling at 850px", narrowRules.tableFits);
await shoot("02-rules-850");
await page.keyboard.press("Escape");

// Bulk categorize: searching adds a checkbox column and, once a row is ticked, a selection bar. Without a search both appear
// only while something is selected. Set category is never clicked.
await page.setViewport({ width: 1728, height: 1000 });
const searchTerm = await page.evaluate(() => document.querySelector(".transactions-table .transaction-merchant")?.textContent?.trim() || "");
check("a transaction merchant is available to search for", searchTerm !== "" && searchTerm !== "—", searchTerm);
check("no checkbox column without a search or selection", await page.$(".transactions-table .select-cell") === null && await page.$(".selection-bar") === null);
await page.type(".transactions-panel .search-input", searchTerm);
await page.waitForSelector(".transactions-table .select-cell input", { timeout: 5000 });
const bulkMetrics = () => page.evaluate(() => {
  const scroller = document.querySelector(".transactions-scroll");
  const bar = document.querySelector(".selection-bar");
  const box = (element) => element?.getBoundingClientRect();
  return {
    tableFits: scroller.scrollWidth <= scroller.clientWidth + 1,
    docWidth: document.documentElement.scrollWidth,
    selectWidth: box(document.querySelector(".transactions-table th.select-cell"))?.width ?? 0,
    barVisible: Boolean(bar),
    barBelowHeading: bar ? box(bar).top >= box(document.querySelector(".transactions-heading")).bottom - 1 : false,
    barAboveTable: bar ? box(bar).bottom <= box(document.querySelector(".transactions-table")).top + 1 : false,
    barLabel: bar?.querySelector("strong")?.textContent ?? "",
    barFits: bar ? box(bar).right <= window.innerWidth : false,
  };
});
await page.click(".transactions-table tbody .select-cell input");
const bulk = await bulkMetrics();
check("selection bar appears after ticking a row", bulk.barVisible && bulk.barLabel === "1 selected", bulk.barLabel);
check("selection bar sits between the heading and the table", bulk.barBelowHeading && bulk.barAboveTable);
check("checkbox column is about 28px wide", Math.abs(bulk.selectWidth - 28) <= 2, `${bulk.selectWidth}px`);
check("transactions table fits with the checkbox column at 1728px", bulk.tableFits && bulk.docWidth <= 1728 + 1, `doc ${bulk.docWidth}`);
for (const width of [1024, 850]) {
  await page.setViewport({ width, height: 1000 });
  await new Promise((resolve) => setTimeout(resolve, 200));
  const narrow = await bulkMetrics();
  check(`transactions table fits with the checkbox column at ${width}px`, narrow.tableFits && narrow.docWidth <= width + 1, `doc ${narrow.docWidth}`);
  check(`selection bar fits at ${width}px`, narrow.barVisible && narrow.barFits);
  if (width === 850) {
    await page.evaluate(() => document.querySelector(".selection-bar").scrollIntoView({ block: "start" }));
    await page.evaluate(() => window.scrollBy(0, -60));
    await shoot("10-bulk-select");
  }
}
await page.evaluate(() => {
  [...document.querySelectorAll(".selection-bar button")].find((button) => button.textContent.trim() === "Clear").click();
});
check("Clear removes the selection bar", await page.$(".selection-bar") === null);
await page.focus(".transactions-panel .search-input");
await page.keyboard.press("Escape");
await page.waitForFunction(() => !document.querySelector(".transactions-table .select-cell"), { timeout: 5000 });
check("clearing the search removes the checkbox column", true);
await page.evaluate(() => window.scrollTo(0, 0));

// Budgets: the live server may predate them, so a second page answers the budgets request itself and
// the Left column is checked at the three widths the year table has to fit.
{
  const budgetPage = await browser.newPage();
  await budgetPage.setViewport({ width: 1728, height: 1000 });
  await budgetPage.setRequestInterception(true);
  budgetPage.on("request", async (request) => {
    const url = new URL(request.url());
    if (url.pathname !== "/api/budgets" || request.method() !== "GET") return request.continue();
    const type = url.searchParams.get("account_type");
    const list = await (await fetch(`${APP_URL}/api/categories?account_type=${type}`)).json();
    const amounts = [1500, 40, 250000, 3000];
    const body = list.filter((category) => category.name !== "Uncategorized").slice(0, amounts.length)
      .map((category, index) => ({ category_id: category.id, year: url.searchParams.get("year"), amount: amounts[index].toFixed(2) }));
    return request.respond({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await budgetPage.goto(APP_URL, { waitUntil: "networkidle0", timeout: 20000 });
  if (await budgetPage.$(".year-summary-table tbody tr") === null) {
    await budgetPage.click("button.account-tab:nth-child(2)");
  }
  await budgetPage.waitForSelector(".year-summary-table tbody tr", { timeout: 10000 });
  for (const width of [1728, 1024, 850]) {
    await budgetPage.setViewport({ width, height: 1000 });
    await new Promise((resolve) => setTimeout(resolve, 300));
    const left = await budgetPage.evaluate(() => {
      const headers = [...document.querySelectorAll(".year-summary-table thead th")];
      const total = headers.findIndex((header) => header.textContent === "Total");
      const scroller = document.querySelector(".year-table-scroll");
      const cells = [...document.querySelectorAll(".year-summary-table .year-category-row")]
        .map((row) => row.children[total + 1]);
      return {
        header: headers[total + 1]?.textContent,
        width: headers[total + 1]?.getBoundingClientRect().width ?? 0,
        columns: headers.length,
        budgeted: cells.filter((cell) => cell.textContent !== "—").length,
        cutOff: cells.some((cell) => cell.scrollWidth > cell.clientWidth + 1),
        tableFits: scroller.scrollWidth <= scroller.clientWidth + 1,
        docWidth: document.documentElement.scrollWidth,
      };
    });
    check(`Left column follows Total with budgets at ${width}px`, left.header === "Left" && left.width > 20 && left.budgeted > 0, JSON.stringify(left));
    check(`year table with the Left column fits at ${width}px`, left.tableFits && !left.cutOff && left.docWidth <= width + 1, JSON.stringify(left));
    const name = width === 1728 ? "12-budget-left" : `12-budget-left-${width}`;
    await budgetPage.screenshot({ path: `${SCREENSHOTS}${name}.png`, fullPage: false });
    shots.push(name);
  }
  await budgetPage.close();
}

check("no console errors", logs.filter((entry) => (entry.startsWith("error") || entry.startsWith("pageerror"))).length === 0, logs.slice(0, 3).join(" | "));

await browser.close();
console.log(`\nscreenshots: ${shots.map((name) => `${SCREENSHOTS}${name}.png`).join(", ")}`);
if (failures.length) {
  console.log(`\n${failures.length} layout check(s) failed`);
  process.exit(1);
}
console.log("\nall layout checks passed");
