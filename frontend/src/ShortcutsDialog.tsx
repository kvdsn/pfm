import { Dialog } from "./Dialog";

export const SHORTCUT_GROUPS: { title: string; shortcuts: [keys: string[], label: string][] }[] = [
  {
    title: "Anywhere",
    shortcuts: [
      [["E"], "personal, business, or kids"],
      [["G"], "balance graph"],
      [["T"], "triage"],
      [["F"], "forecast: the plan, recurring payments and their inputs"],
      [["C"], "categories"],
      [["R"], "rules"],
      [["A"], "accounts, imports and backups"],
      [["S"], "show or hide the forecast"],
      [["I"], "import files"],
      [["K"], "keyboard shortcuts"],
      [["Esc"], "close, deselect ticked transactions, unselect, or clear filter"],
    ],
  },
  {
    title: "Overview",
    shortcuts: [
      [["←", "→"], "previous or next year"],
      [["/"], "search"],
      [["↑", "↓"], "move through transactions"],
      [["Enter"], "set the category of the selected transaction, or of the ticked ones"],
      [["Enter"], "in the bulk bar, choose, then apply"],
      [["U"], "next uncategorized transaction"],
      [["O"], "open the selected transaction's rule"],
      [["Shift+↑/↓"], "extend the range"],
      [["X"], "check or uncheck the active row or range"],
      [["M"], "flag the selected transaction for follow-up and add a note, or remove the flag"],
      [["Shift+M"], "show follow-ups, or all transactions"],
      [["D"], "mark the selected follow-up done, or reopen it"],
    ],
  },
  {
    title: "Triage view",
    shortcuts: [
      [["↑", "↓"], "move through groups"],
      [["Enter"], "choose the category of the selected group"],
    ],
  },
  {
    title: "Forecast view",
    shortcuts: [
      [["N"], "add a planned item, or after a click in recurring payments, add a payment"],
      [["↑", "↓"], "move through recurring payments"],
      [["Enter"], "confirm the selected suggestion"],
    ],
  },
  {
    title: "Rules view",
    shortcuts: [
      [["↑", "↓"], "move through rules"],
      [["N"], "new rule"],
    ],
  },
  {
    title: "Categories view",
    shortcuts: [
      [["↑", "↓"], "move through categories"],
      [["N"], "new category"],
    ],
  },
  {
    title: "Accounts view",
    shortcuts: [
      [["↑", "↓"], "move through accounts, or after a click in backups, through backups"],
      [["N"], "import files, or after a click in backups, a new backup note"],
    ],
  },
];

/** Groups before this index go in the left column, the rest in the right one. */
const COLUMN_SPLIT = 2;

/** Lists the keyboard shortcuts; the only place besides the footer hint where they are shown. */
export function ShortcutsDialog({ onClose }: { onClose: () => void }) {
  return (
    <Dialog
      title="Keyboard shortcuts"
      closeLabel="Close keyboard shortcuts"
      className="shortcuts-dialog"
      backdropClassName="shortcuts-backdrop"
      onClose={onClose}
    >
      <div className="shortcuts-columns">
        {[SHORTCUT_GROUPS.slice(0, COLUMN_SPLIT), SHORTCUT_GROUPS.slice(COLUMN_SPLIT)].map((groups, column) => (
          <div key={column}>
            {groups.map((group) => (
              <section key={group.title} className="shortcut-group" aria-label={group.title}>
                <h4>{group.title}</h4>
                <dl className="shortcut-list">
                  {group.shortcuts.map(([keys, label]) => (
                    <div key={label}>
                      <dt>
                        {keys.map((key, index) => (
                          <span key={key}>{index > 0 && "/"}<kbd>{key}</kbd></span>
                        ))}
                      </dt>
                      <dd>{label}</dd>
                    </div>
                  ))}
                </dl>
              </section>
            ))}
          </div>
        ))}
      </div>
    </Dialog>
  );
}
