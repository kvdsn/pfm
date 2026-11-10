import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BackupsView } from "./Backups";
import type { Backup } from "./types";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({ api: apiMock, errorMessage: (_: unknown, fallback: string) => fallback }));

const backup = (day: number, label = "", size = 2_500_000, automatic = false): Backup => ({
  name: `finance-backup-202610${String(day).padStart(2, "0")}-101500${label ? `-${label.replace(/ /g, "-")}` : ""}.sqlite3`,
  created_at: `2026-10-${String(day).padStart(2, "0")}T10:15:00`,
  label,
  automatic,
  size_bytes: size,
});

let backups: Backup[] = [];

beforeEach(() => {
  apiMock.mockReset();
  backups = [backup(17, "before rule edits"), backup(3)];
  apiMock.mockImplementation(async (url: string, init?: { method?: string; json?: { label: string } }) => {
    if (url === "/api/backups" && init?.method === "POST") return backup(18, init.json?.label ?? "");
    if (url === "/api/backups") return backups;
    throw new Error(`Unexpected API call: ${url}`);
  });
});

afterEach(cleanup);

const renderView = (now?: Date) => {
  const props = { onNotice: vi.fn(), onError: vi.fn(), onClose: vi.fn(), onRestored: vi.fn().mockResolvedValue(undefined) };
  render(<BackupsView imports={[]} now={now} {...props} />);
  return props;
};

const backupRows = () => [...screen.getByRole("table").querySelectorAll("tbody tr")];

describe("Backups view", () => {
  it("warns only when the newest backup is more than 30 days old", async () => {
    renderView(new Date(2026, 10, 28, 12));
    expect(await screen.findByText("before rule edits")).toBeInTheDocument();
    expect(screen.getByText("The latest backup is 42 days old.")).toBeInTheDocument();
    cleanup();
    renderView(new Date(2026, 10, 10, 12));
    expect(await screen.findByText("before rule edits")).toBeInTheDocument();
    expect(screen.queryByText(/latest backup is/)).not.toBeInTheDocument();
  });

  it("lists backups as returned, with date, note, size, and file name", async () => {
    renderView();
    expect(await screen.findByText("before rule edits")).toBeInTheDocument();
    const rows = backupRows();
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain(new Date(backups[0].created_at).toLocaleString());
    expect(rows[0].textContent).toContain("2.5 MB");
    expect(rows[0].textContent).toContain("finance-backup-20261017-101500-before-rule-edits.sqlite3");
    expect(rows[1].textContent).toContain("finance-backup-20261003-101500.sqlite3");
  });

  it("tags automatic backups with a muted auto marker", async () => {
    backups = [backup(17, "before import", 2_500_000, true), backup(3, "mine")];
    renderView();
    await screen.findByText("before import");
    const rows = backupRows();
    expect(rows[0].querySelector("td:nth-child(2)")?.textContent).toBe("before import auto");
    expect(rows[1].querySelector("td:nth-child(2)")?.textContent).toBe("mine");
    expect(screen.getByText(/keeps the latest 20 of those; backups you make are kept until you delete them/)).toBeInTheDocument();
  });

  it("shows an empty state", async () => {
    backups = [];
    renderView();
    expect(await screen.findByText("No backups yet.")).toBeInTheDocument();
  });

  it("posts the note, reports the new file, clears the note, and reloads the list", async () => {
    const { onNotice } = renderView();
    await screen.findByText("before rule edits");
    fireEvent.change(screen.getByLabelText("Backup note"), { target: { value: "tidy up" } });
    fireEvent.click(screen.getByRole("button", { name: "Back up now" }));
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith("Backed up to finance-backup-20261018-101500-tidy-up.sqlite3."));
    expect(apiMock).toHaveBeenCalledWith("/api/backups", { method: "POST", json: { label: "tidy up" } });
    await waitFor(() => expect(screen.getByLabelText("Backup note")).toHaveValue(""));
    await waitFor(() => expect(apiMock.mock.calls.filter(([url, init]) => url === "/api/backups" && !init)).toHaveLength(2));
  });

  it("backs up on Enter in the note field", async () => {
    const { onNotice } = renderView();
    await screen.findByText("before rule edits");
    fireEvent.keyDown(screen.getByLabelText("Backup note"), { key: "Enter" });
    await waitFor(() => expect(onNotice).toHaveBeenCalled());
  });

  it("reports a failed backup through onError", async () => {
    const { onError } = renderView();
    await screen.findByText("before rule edits");
    apiMock.mockRejectedValueOnce(new Error("disk full"));
    fireEvent.click(screen.getByRole("button", { name: "Back up now" }));
    await waitFor(() => expect(onError).toHaveBeenCalledWith("Could not back up"));
  });

  it("lists every backup, with the count in the heading", async () => {
    backups = Array.from({ length: 13 }, (_, index) => backup(28 - index));
    renderView();
    await waitFor(() => expect(backupRows()).toHaveLength(13));
    expect(screen.getByRole("heading", { name: /Backups\s*13/ })).toBeInTheDocument();
    expect(screen.queryByText(/older/)).not.toBeInTheDocument();
  });
});

describe("Restoring a backup", () => {
  const restoreUrl = `/api/backups/${encodeURIComponent("finance-backup-20261017-101500-before-rule-edits.sqlite3")}/restore`;
  const restoreCalls = () => apiMock.mock.calls.filter(([url]) => String(url).endsWith("/restore"));
  afterEach(() => vi.restoreAllMocks());

  it("asks for confirmation and makes no call when cancelled", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { onNotice, onRestored } = renderView();
    await screen.findByText("before rule edits");
    fireEvent.click(screen.getAllByRole("button", { name: "Restore" })[0]);
    const date = new Date(backups[0].created_at).toLocaleString();
    expect(confirm).toHaveBeenCalledWith(
      `Restore the backup from ${date} (before rule edits)? Your current data will be replaced by this backup. A backup of your current data is made first.`,
    );
    expect(restoreCalls()).toHaveLength(0);
    expect(onNotice).not.toHaveBeenCalled();
    expect(onRestored).not.toHaveBeenCalled();
  });

  it("omits the label from the confirmation when there is none", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderView();
    await screen.findByText("before rule edits");
    fireEvent.click(screen.getAllByRole("button", { name: "Restore" })[1]);
    expect(confirm.mock.calls[0][0]).toMatch(/^Restore the backup from [^(]*\? Your current data/);
  });

  it("posts to the encoded URL, reports the safety backup, and calls onRestored", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const safety = "finance-backup-20261018-101500-before-restoring.sqlite3";
    const base = apiMock.getMockImplementation()!;
    apiMock.mockImplementation(async (url: string, init?: { method?: string }) => {
      if (url === restoreUrl) return { restored: backups[0], safety_backup: safety };
      return base(url, init);
    });
    const { onNotice, onRestored } = renderView();
    await screen.findByText("before rule edits");
    fireEvent.click(screen.getAllByRole("button", { name: "Restore" })[0]);
    const date = new Date(backups[0].created_at).toLocaleString();
    await waitFor(() => expect(onNotice).toHaveBeenCalledWith(
      `Restored the backup from ${date}. Your previous data was saved as ${safety}.`,
    ));
    expect(apiMock).toHaveBeenCalledWith(restoreUrl, { method: "POST" });
    await waitFor(() => expect(onRestored).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(apiMock.mock.calls.filter(([url, init]) => url === "/api/backups" && !init)).toHaveLength(2));
  });

  it("reports a failed restore through onError without reloading the app", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { onError, onRestored } = renderView();
    await screen.findByText("before rule edits");
    apiMock.mockRejectedValueOnce(new Error("broken"));
    fireEvent.click(screen.getAllByRole("button", { name: "Restore" })[0]);
    await waitFor(() => expect(onError).toHaveBeenCalledWith("Could not restore the backup"));
    expect(onRestored).not.toHaveBeenCalled();
  });

  it("selects a backup row by clicking or with the arrow keys, but not while typing a note", async () => {
    renderView();
    await screen.findByText("before rule edits");
    expect(backupRows().filter((row) => row.classList.contains("selected-rule"))).toHaveLength(0);
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(backupRows()[0]).toHaveClass("selected-rule");
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(backupRows()[1]).toHaveClass("selected-rule");
    fireEvent.keyDown(screen.getByLabelText("Backup note"), { key: "ArrowUp" });
    expect(backupRows()[1]).toHaveClass("selected-rule");
    fireEvent.click(backupRows()[0]);
    expect(backupRows()[0]).toHaveClass("selected-rule");
    expect(backupRows()[1]).not.toHaveClass("selected-rule");
  });

  it("focuses the note field when the new item signal changes", async () => {
    const props = { onNotice: vi.fn(), onError: vi.fn(), onClose: vi.fn(), onRestored: vi.fn().mockResolvedValue(undefined) };
    const { rerender } = render(<BackupsView imports={[]} {...props} newItemSignal={0} />);
    await screen.findByText("before rule edits");
    expect(screen.getByLabelText("Backup note")).not.toHaveFocus();
    rerender(<BackupsView imports={[]} {...props} newItemSignal={1} />);
    expect(screen.getByLabelText("Backup note")).toHaveFocus();
  });
});
