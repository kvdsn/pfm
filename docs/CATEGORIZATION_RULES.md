# Categorization rules

Each entity (Personal, Business, Kids) has its own categories and rules.

## Matching

- A rule matches its text in any combination of the **merchant**, **description** and **memo**, ignoring case.
- **Match modes:**
  - **contains:** the text appears anywhere in the field.
  - **word:** the text appears as a whole word, with no letter or digit directly before or after it. So "Zara" matches "ZARA.COM" and "Zara 392", but not "Zarakine".
  - **is:** the text is the field's whole text.

  `word` and `is` also ignore repeated spaces.
- **Priority:** rules are tried in their priority order (drag to reorder in the Rules page), and the first match wins.
- **No match:** the transaction is `Uncategorized`.
- **What the fields contain:**
  - **Merchant:** the counterparty. When the bank gives none, such as for KBC card payments, it is the shop named in the description. It is never the account holder.
  - **Description:** the bank's transaction text.
  - **Memo:** the free-text message, or the structured reference.
- **Never matched:** notes and follow-ups.
- **No guessing:** the app never guesses from amounts, direction, refund wording or history.

## Who set the category

Each transaction remembers how its category was set:

- **Rule #N:** by a rule. Click it to open the rule.
- **Hand:** by you, in the transactions table, the Triage page or bulk categorizing.
- **A dash:** by default, because no rule matched.

A category you set by hand is never changed by applying rules. Changing a category never creates or changes a rule. The app may offer to make one, and does only when you accept.

## Saving and applying

- **Saving** a rule never changes transactions. The **Impact** pane previews the draft:
  - what it matches
  - what it decides, meaning matches that no earlier rule takes
  - what would change
  - transactions it would release
- **Applying** a rule gives its category to every transaction it decides, except hand-set ones. Transactions it categorized before but no longer decides move to the next matching rule, or to `Uncategorized`. There is no "reapply all rules" action.
- **Deleting** a rule moves only the transactions it categorized to `Uncategorized`.
- **Triage's Make rule** creates an exact merchant rule from a group and applies it.

## Categories

- **Income or expense:** a category's **Income** setting decides how its transactions count in reports. See [Reports](REQUIREMENTS.md#reports).
- **Flow type:** a rule also sets a flow type (income, expense or transfer). It only matters for `Uncategorized` transactions.
- **Removing** a category moves its transactions to `Uncategorized` and deletes its rules and budgets.
- **Merging** a category moves its transactions, rules and recurring payments to the target. Transactions keep how they were set, and budgets of the same year are added together.
- **Uncategorized** can't be renamed, removed, merged away or budgeted.
- **Backups:** the app makes a backup before removing or merging a category, and before applying or deleting a rule (at most once per 30 minutes). If the backup fails, nothing changes.

## Not decided by rules

- **Transfers between your own accounts** in the same entity are recognized by the counterparty IBAN. They have no category to set and never count in totals.
- **Card settlements** replaced by their statement's purchases keep their category but don't count in totals. Rules that match them show an "itemized" count.
- **Refunds** are not detected automatically. A refund in an expense category lowers that category's expense. Without a matching rule, it stays `Uncategorized`.
