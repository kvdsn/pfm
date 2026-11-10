import { useCallback, useEffect, useRef, useState } from "react";
import type { Backup, ImportRecord } from "./types";
import { api, errorMessage } from "./api";
import { useArrowSelection, useSignal } from "./hooks";

const STALE_BACKUP_DAYS = 30;

/** The full-page backups view: lists every database backup and makes a new one, optionally with a short note in its name. */
export function BackupsView({
  imports,
  onNotice,
  onError,
  onRestored,
  onClose,
  newItemSignal = 0,
  active = true,
  now = new Date(),
}: {
  /** The current moment, injectable for tests. */
  now?: Date;
  /** Whether the arrow keys move through the backups; the Accounts page shares them with its accounts. */
  active?: boolean;
  /** Focuses the note field each time this counter changes, such as from a keyboard shortcut. */
  newItemSignal?: number;
  imports: ImportRecord[];
  onNotice: (message: string) => void;
  onError: (message: string) => void;
  onRestored: () => Promise<void>;
  /** Omitted when the backups are a section of another page, which has its own close button. */
  onClose?: () => void;
}) {
  const [backups, setBackups] = useState<Backup[]>([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const tableBody = useRef<HTMLTableSectionElement>(null);
  const noteInput = useRef<HTMLInputElement>(null);
  useArrowSelection(backups.map((backup) => backup.name), selectedName, (name) => {
    setSelectedName(name);
    tableBody.current?.querySelector(`[data-backup-name="${CSS.escape(name)}"]`)?.scrollIntoView?.({ block: "nearest" });
  }, active);
  useSignal(newItemSignal, () => noteInput.current?.focus());

  const load = useCallback(() => api<Backup[]>("/api/backups").then(setBackups)
    .catch((cause) => onError(errorMessage(cause, "Could not load backups"))), [onError]);
  // Importing makes a backup, so reload whenever the import history changes.
  useEffect(() => {
    void load();
  }, [load, imports]);

  const backUp = async () => {
    setBusy(true);
    onError("");
    try {
      const created = await api<Backup>("/api/backups", { method: "POST", json: { label: note } });
      onNotice(`Backed up to ${created.name}.`);
      setNote("");
      await load();
    } catch (cause) {
      onError(errorMessage(cause, "Could not back up"));
    } finally {
      setBusy(false);
    }
  };

  const restore = async (backup: Backup) => {
    const date = new Date(backup.created_at).toLocaleString();
    const label = backup.label ? ` (${backup.label})` : "";
    if (!window.confirm(
      `Restore the backup from ${date}${label}? Your current data will be replaced by this backup. A backup of your current data is made first.`,
    )) return;
    setBusy(true);
    onError("");
    try {
      const result = await api<{ safety_backup: string | null }>(
        `/api/backups/${encodeURIComponent(backup.name)}/restore`,
        { method: "POST" },
      );
      onNotice(`Restored the backup from ${date}. Your previous data was saved as ${result.safety_backup}.`);
      await load();
      await onRestored();
    } catch (cause) {
      onError(errorMessage(cause, "Could not restore the backup"));
    } finally {
      setBusy(false);
    }
  };

  const newest = backups[0];
  const newestAge = newest ? Math.floor((now.getTime() - new Date(newest.created_at).getTime()) / 86_400_000) : 0;

  return (
    <div className="rules-view single-pane">
      <section className="panel rules-pane" aria-label="Backups">
        <div className="section-heading">
          <div><h3>Backups <span className="count">{backups.length}</span></h3></div>
          <div className="transaction-filters">
            <input
              ref={noteInput}
              className="search-input"
              value={note}
              maxLength={40}
              placeholder="Note (optional)"
              aria-label="Backup note"
              disabled={busy}
              onChange={(event) => setNote(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter" && !busy) void backUp(); }}
            />
            <button className="button secondary" disabled={busy} onClick={() => void backUp()}>Back up now</button>
            {onClose && <button className="icon-button" aria-label="Close backups" onClick={onClose}>×</button>}
          </div>
        </div>
        <p className="muted small">
          The app backs up automatically before imports, restores, and other bulk changes, and keeps the latest 20 of those; backups you make are kept until you delete them. Restoring replaces your current data after backing it up first.
        </p>
        {newestAge > STALE_BACKUP_DAYS && <p className="muted small">The latest backup is {newestAge} days old.</p>}
        <div className="table-scroll">
          <table className="rules-table backups-table">
            <thead>
              <tr>
                <th>Date</th><th>Note</th><th className="numeric">Size</th><th>File</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody ref={tableBody}>
              {backups.map((backup) => (
                <tr
                  key={backup.name}
                  data-backup-name={backup.name}
                  className={backup.name === selectedName ? "selected-rule" : ""}
                  onClick={() => setSelectedName(backup.name)}
                >
                  <td className="nowrap">{new Date(backup.created_at).toLocaleString()}</td>
                  <td className="nowrap">{backup.label}{backup.automatic && <span className="muted small"> auto</span>}</td>
                  <td className="numeric">{(backup.size_bytes / 1_000_000).toFixed(1)} MB</td>
                  <td className="muted truncate-cell" title={backup.name}>{backup.name}</td>
                  <td className="rules-actions">
                    <button className="button secondary" disabled={busy} onClick={() => void restore(backup)}>Restore</button>
                  </td>
                </tr>
              ))}
              {!backups.length && (
                <tr><td colSpan={5} className="muted">No backups yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
