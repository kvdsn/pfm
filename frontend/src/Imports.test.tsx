import "@testing-library/jest-dom/vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useImportFlow } from "./Imports";
import type { Backup, ImportPreview, ImportRecord } from "./types";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({ api: apiMock, errorMessage: (_: unknown, fallback: string) => fallback }));

const backup = (day: number, label = ""): Backup => ({
  name: `finance-backup-202610${String(day).padStart(2, "0")}-101500.sqlite3`,
  created_at: `2026-10-${String(day).padStart(2, "0")}T10:15:00`,
  label,
  automatic: label === "before import",
  size_bytes: 2_500_000,
});

beforeEach(() => {
  apiMock.mockReset();
});
afterEach(cleanup);

describe("commitImport", () => {
  const preview = (token: string, filename: string): ImportPreview => ({
    token,
    filename,
    masked_iban: "BE00 **** 0001",
    account_label: "Checking",
    account_type: "personal",
    account_type_required: false,
    transaction_count: 3,
    duplicate_count: 0,
    new_count: 3,
    unknown_columns: [],
    preview: [],
    sample_truncated: false,
  });

  const renderFlow = async () => {
    const callbacks = { onImported: vi.fn(async () => {}), onUndone: vi.fn(async () => {}), onError: vi.fn(), onNotice: vi.fn() };
    apiMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/api/import/preview")) return preview(url.includes("a.csv") ? "tok-a" : "tok-b", "x.csv");
      if (url.endsWith("/commit")) return { imported: 3, duplicates_skipped: 1 };
      if (url === "/api/backups") return backup(18, "before import");
      throw new Error(`Unexpected API call: ${url}`);
    });
    const hook = renderHook(() => useImportFlow(callbacks));
    await act(() => hook.result.current.previewFiles([new File(["x"], "a.csv"), new File(["x"], "b.csv")]));
    apiMock.mockClear();
    return { hook, ...callbacks };
  };

  const urls = () => apiMock.mock.calls.map(([url]) => url);

  it("backs up once, before the first commit of a batch", async () => {
    const { hook, onImported } = await renderFlow();
    await act(() => hook.result.current.commitImport());
    expect(urls()).toEqual(["/api/backups", "/api/import/tok-a/commit", "/api/import/tok-b/commit"]);
    expect(apiMock).toHaveBeenNthCalledWith(1, "/api/backups", { method: "POST", json: { label: "before import", automatic: true } });
    expect(onImported).toHaveBeenCalledWith("personal");
  });

  it("commits files with their own balances first, then deferred ones newest first", async () => {
    const callbacks = { onImported: vi.fn(async () => {}), onUndone: vi.fn(async () => {}), onError: vi.fn(), onNotice: vi.fn() };
    const previews: Record<string, Partial<ImportPreview>> = {
      "a.csv": { balances_deferred: true, last_date: "2026-01-31" },
      "b.csv": {},
      "c.csv": { balances_deferred: true, last_date: "2026-03-31" },
      "d.csv": { balances_deferred: false },
    };
    apiMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/api/import/preview")) {
        const name = decodeURIComponent(url.split("filename=")[1]);
        return { ...preview(`tok-${name[0]}`, name), ...previews[name] };
      }
      if (url.endsWith("/commit")) return { imported: 1, duplicates_skipped: 0 };
      return backup(18, "before import");
    });
    const hook = renderHook(() => useImportFlow(callbacks));
    await act(() => hook.result.current.previewFiles(Object.keys(previews).map((name) => new File(["x"], name))));
    apiMock.mockClear();
    await act(() => hook.result.current.commitImport());
    expect(urls().filter((url) => url.endsWith("/commit"))).toEqual([
      "/api/import/tok-b/commit", "/api/import/tok-d/commit", "/api/import/tok-c/commit", "/api/import/tok-a/commit",
    ]);
  });

  it("mentions files whose copy could not be saved", async () => {
    const { hook, onNotice } = await renderFlow();
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/import/tok-a/commit") return { imported: 3, duplicates_skipped: 0, source_file_error: "Disk full" };
      if (url.endsWith("/commit")) return { imported: 3, duplicates_skipped: 0, source_file: "personal/x.csv" };
      return backup(18, "before import");
    });
    await act(() => hook.result.current.commitImport());
    expect(onNotice).toHaveBeenCalledWith("Imported 6 transactions; skipped 0 duplicates. Could not save a copy in source-data of x.csv (Disk full).");
  });

  it("imports a new account as kids when chosen", async () => {
    const callbacks = { onImported: vi.fn(async () => {}), onUndone: vi.fn(async () => {}), onError: vi.fn(), onNotice: vi.fn() };
    apiMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/api/import/preview")) return { ...preview("tok-k", "k.csv"), account_type: null, account_type_required: true };
      if (url.endsWith("/commit")) return { imported: 3, duplicates_skipped: 0 };
      if (url === "/api/backups") return backup(18, "before import");
      throw new Error(`Unexpected API call: ${url}`);
    });
    const hook = renderHook(() => useImportFlow(callbacks));
    await act(() => hook.result.current.previewFiles([new File(["x"], "k.csv")]));
    expect(hook.result.current.importTypeOf(hook.result.current.previews[0])).toBe("");
    act(() => hook.result.current.setImportType(hook.result.current.previews[0], "kids"));
    await act(() => hook.result.current.commitImport());
    expect(apiMock).toHaveBeenCalledWith("/api/import/tok-k/commit", { method: "POST", json: { account_type: "kids" } });
    expect(callbacks.onImported).toHaveBeenCalledWith("kids");
  });

  it("imports nothing when the backup fails", async () => {
    const { hook, onError, onImported } = await renderFlow();
    apiMock.mockRejectedValueOnce(new Error("disk full"));
    await act(() => hook.result.current.commitImport());
    expect(urls()).toEqual(["/api/backups"]);
    // The mocked errorMessage always returns the fallback, so only the error path is checked here.
    expect(onError).toHaveBeenLastCalledWith("Could not import");
    expect(onImported).not.toHaveBeenCalled();
    expect(hook.result.current.previews).toHaveLength(2);
  });
});

describe("undoImport", () => {
  const record = (annotated?: number): ImportRecord => ({
    id: 5, filename: "kbc.csv", imported_at: "2026-10-01T10:00:00", row_count: 3, inserted_count: 3,
    duplicate_count: 0, transaction_count: 3, masked_iban: "BE00 **** 0001", account_label: "Checking", annotated,
  });
  const confirmText = async (annotated?: number) => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const hook = renderHook(() => useImportFlow({
      onImported: vi.fn(async () => {}), onUndone: vi.fn(async () => {}), onError: vi.fn(), onNotice: vi.fn(),
    }));
    await act(() => hook.result.current.undoImport(record(annotated)));
    const text = String(confirm.mock.calls[0][0]);
    confirm.mockRestore();
    return text;
  };

  it("warns about notes and follow-ups that would be lost, in singular and plural", async () => {
    expect(await confirmText(1)).toContain("1 transaction with notes or follow-ups will lose them.");
    expect(await confirmText(3)).toContain("3 transactions with notes or follow-ups will lose them.");
  });

  it("adds no warning without annotated transactions", async () => {
    expect(await confirmText(0)).not.toContain("follow-ups");
    expect(await confirmText(undefined)).not.toContain("follow-ups");
  });
});
