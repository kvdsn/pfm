import type React from "react";
import { useState } from "react";
import { ACCOUNT_TYPES, ACCOUNT_TYPE_LABELS, type AccountType, type Backup, type ImportPreview, type ImportRecord } from "./types";
import { api, errorMessage } from "./api";
import { money } from "./format";

/** The key shared by previews of the same account, so one choice maps all of its files. */
const accountKey = (preview: ImportPreview) => `${preview.account_label} ${preview.masked_iban}`;

/** State and actions for previewing, committing, and undoing imports of one or more files. */
export function useImportFlow({
  onImported,
  onUndone,
  onError,
  onNotice,
}: {
  onImported: (accountType: AccountType) => Promise<void>;
  onUndone: () => Promise<void>;
  onError: (message: string) => void;
  onNotice: (message: string) => void;
}) {
  const [previews, setPreviews] = useState<ImportPreview[]>([]);
  const [importTypes, setImportTypes] = useState<Record<string, AccountType | "">>({});
  const [busy, setBusy] = useState(false);

  const run = async (fallbackError: string, action: () => Promise<void>) => {
    setBusy(true);
    onError("");
    try {
      await action();
    } catch (cause) {
      onError(errorMessage(cause, fallbackError));
    } finally {
      setBusy(false);
    }
  };

  const discardPreviews = async () => {
    await Promise.all(previews.map((preview) => api(`/api/import/${preview.token}/cancel`, { method: "DELETE" })));
    setPreviews([]);
  };

  const previewFiles = (files: File[]) => run("Could not read the file", async () => {
    onNotice("");
    await discardPreviews();
    const staged: ImportPreview[] = [];
    const failures: string[] = [];
    for (const file of files) {
      try {
        staged.push(await api<ImportPreview>(
          `/api/import/preview?filename=${encodeURIComponent(file.name)}`,
          { method: "POST", body: file },
        ));
      } catch (cause) {
        failures.push(`${file.name}: ${errorMessage(cause, "could not be read")}`);
      }
    }
    setPreviews(staged);
    setImportTypes(Object.fromEntries(staged.map((preview) => [accountKey(preview), preview.account_type || ""])));
    if (failures.length) throw new Error(`Skipped ${failures.length} file${failures.length === 1 ? "" : "s"}: ${failures.join("; ")}`);
  });

  const setImportType = (preview: ImportPreview, accountType: AccountType | "") =>
    setImportTypes((current) => ({ ...current, [accountKey(preview)]: accountType }));
  const importTypeOf = (preview: ImportPreview) => importTypes[accountKey(preview)] ?? "";

  const cancelPreview = () => run("Could not discard import preview", discardPreviews);

  const commitImport = () => run("Could not import", async () => {
    if (!previews.length || previews.some((preview) => !importTypeOf(preview))) return;
    // One safety copy per batch; nothing is imported if it cannot be made.
    try {
      await api<Backup>("/api/backups", { method: "POST", json: { label: "before import", automatic: true } });
    } catch (cause) {
      throw new Error(`Could not back up before importing, so nothing was imported: ${errorMessage(cause, "unknown error")}`);
    }
    let imported = 0;
    let skipped = 0;
    const unsaved: string[] = [];
    const remaining = [...previews];
    try {
      while (remaining.length) {
        const preview = remaining[0];
        const result = await api<{ imported: number; duplicates_skipped: number; source_file_error?: string }>(
          `/api/import/${preview.token}/commit`,
          { method: "POST", json: { account_type: importTypeOf(preview) } },
        );
        imported += result.imported;
        skipped += result.duplicates_skipped;
        if (result.source_file_error) unsaved.push(`${preview.filename} (${result.source_file_error})`);
        remaining.shift();
      }
    } finally {
      setPreviews(remaining);
      if (imported || skipped) {
        const copies = unsaved.length ? ` Could not save a copy in source-data of ${unsaved.join("; ")}.` : "";
        onNotice(`Imported ${imported} transactions; skipped ${skipped} duplicates.${copies}`);
      }
    }
    await onImported(importTypeOf(previews[previews.length - 1]) as AccountType);
  });

  const undoImport = (record: ImportRecord) => {
    const count = record.transaction_count;
    const detail = count
      ? ` This will permanently remove ${count} transaction${count === 1 ? "" : "s"} from reports.`
      : " This import has no remaining transactions to remove.";
    const annotated = record.annotated ?? 0;
    const annotations = annotated
      ? ` ${annotated} transaction${annotated === 1 ? "" : "s"} with notes or follow-ups will lose them.`
      : "";
    if (!window.confirm(`Undo import “${record.filename}”?${detail}${annotations} Account mappings and categories will be kept.`)) {
      return Promise.resolve();
    }
    return run("Could not undo import", async () => {
      const result = await api<{ removed_transactions: number }>(`/api/imports/${record.id}`, { method: "DELETE" });
      const removed = result.removed_transactions;
      onNotice(`Undid import; removed ${removed} transaction${removed === 1 ? "" : "s"}.`);
      await onUndone();
    });
  };

  return {
    previews, importTypeOf, setImportType, busy, previewFiles, cancelPreview, commitImport, undoImport,
  };
}

export type ImportFlow = ReturnType<typeof useImportFlow>;

/** A label styled as a button that opens a chooser for bank and card exports. */
export function ImportFileInput({
  className,
  disabled,
  inputRef,
  onFiles,
  children,
}: {
  className: string;
  disabled?: boolean;
  inputRef?: React.RefObject<HTMLInputElement>;
  onFiles: (files: File[]) => void;
  children: React.ReactNode;
}) {
  return (
    <label className={className}>
      {children}
      <input
        ref={inputRef}
        type="file"
        accept=".csv,text/csv,.pdf,application/pdf"
        multiple
        disabled={disabled}
        onChange={(event) => {
          const files = [...(event.target.files ?? [])];
          if (files.length) onFiles(files);
          event.currentTarget.value = "";
        }}
      />
    </label>
  );
}

function AccountTypeSelect({
  value,
  onChange,
}: {
  value: AccountType | "";
  onChange: (value: AccountType | "") => void;
}) {
  return (
    <select value={value} aria-label="Account type" onChange={(event) => onChange(event.target.value as AccountType | "")}>
      <option value="">Choose personal, business, or kids…</option>
      {ACCOUNT_TYPES.map((kind) => (
        <option key={kind} value={kind}>
          {ACCOUNT_TYPE_LABELS[kind]}
        </option>
      ))}
    </select>
  );
}

export function ImportPreviewPanel({ flow }: { flow: ImportFlow }) {
  const { previews, busy } = flow;
  if (!previews.length) return null;
  const ready = previews.every((preview) => flow.importTypeOf(preview));
  const actions = (
    <div className="panel-actions">
      <button className="button secondary" disabled={busy} onClick={() => void flow.cancelPreview()}>Cancel</button>
      <button className="button primary" disabled={busy || !ready} onClick={() => void flow.commitImport()}>
        {previews.length === 1 ? "Confirm import" : `Import ${previews.length} files`}
      </button>
    </div>
  );
  if (previews.length > 1) {
    const accounts = [...new Map(previews.map((preview) => [accountKey(preview), preview])).values()];
    const totals = (account: ImportPreview) => previews
      .filter((preview) => accountKey(preview) === accountKey(account))
      .reduce((sum, preview) => ({
        files: sum.files + 1, added: sum.added + preview.new_count, skipped: sum.skipped + preview.duplicate_count,
      }), { files: 0, added: 0, skipped: 0 });
    return (
      <section className="panel import-panel">
        <div className="section-heading">
          <div>
            <p className="eyebrow">IMPORT PREVIEW</p>
            <h2>{previews.length} files</h2>
          </div>
          <button className="icon-button" disabled={busy} onClick={() => void flow.cancelPreview()} aria-label="Close preview">×</button>
        </div>
        <div className="table-scroll">
          <table className="preview-table import-accounts-table">
            <thead>
              <tr><th>Account</th><th>Files</th><th>New</th><th>Already imported</th><th>Overview</th></tr>
            </thead>
            <tbody>
              {accounts.map((account) => {
                const { files, added, skipped } = totals(account);
                return (
                  <tr key={accountKey(account)}>
                    <td>{account.account_label} {account.masked_iban}</td>
                    <td>{files}</td>
                    <td>{added}</td>
                    <td>{skipped}</td>
                    <td>
                      <AccountTypeSelect
                        value={flow.importTypeOf(account)}
                        onChange={(value) => flow.setImportType(account, value)}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {actions}
      </section>
    );
  }
  const [preview] = previews;
  const importType = flow.importTypeOf(preview);
  return (
    <section className="panel import-panel">
      <div className="section-heading">
        <div>
          <p className="eyebrow">IMPORT PREVIEW</p>
          <h2>{preview.filename}</h2>
          <p className="muted">{preview.account_label} {preview.masked_iban} · {preview.transaction_count} transactions</p>
        </div>
        <button className="icon-button" disabled={busy} onClick={() => void flow.cancelPreview()} aria-label="Close preview">×</button>
      </div>
      {preview.unknown_columns.length > 0 && (
        <p className="warning">
          Unrecognized columns: {preview.unknown_columns.join(", ")}. They will be preserved with the import.
        </p>
      )}
      <label className="mapping-choice">
        {preview.account_type_required ? "Assign this account to" : "Saved mapping"}
        <AccountTypeSelect value={importType} onChange={(value) => flow.setImportType(preview, value)} />
      </label>
      {!preview.account_type_required && importType && importType !== preview.account_type && (
        <p className="warning">
          This changes the saved account mapping and moves its existing transactions to the {importType} overview.
        </p>
      )}
      <p className="muted import-counts">
        {preview.new_count} new · {preview.duplicate_count} already imported and will be skipped
      </p>
      <div className="table-scroll">
        <table className="preview-table">
          <thead>
            <tr><th>Date</th><th>Merchant</th><th>Description</th><th>Memo</th><th>Amount</th><th>Suggested category</th></tr>
          </thead>
          <tbody>
            {preview.preview.map((item, index) => (
              <tr key={`${item.date}-${index}`}>
                <td>{item.date}</td>
                <td>{item.merchant || "—"}</td>
                <td className="description-cell">{item.description || "—"}</td>
                <td className="description-cell">{item.memo || "—"}</td>
                <td className={Number(item.amount) < 0 ? "negative" : "positive"}>{money(item.amount)}</td>
                <td>{importType ? item.suggestions[importType].category : "Select account type"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {preview.sample_truncated && (
          <p className="muted small">Showing first 10; all {preview.transaction_count} rows will be imported.</p>
        )}
      </div>
      {actions}
    </section>
  );
}
