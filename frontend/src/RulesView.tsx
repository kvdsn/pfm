import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type React from "react";
import type { AccountType, CategoryRule, FlowType, ManagedCategory, MatchMode, RuleHealth, RuleImpact } from "./types";
import { api, errorMessage } from "./api";
import { money } from "./format";
import { clearOnEscape, useArrowSelection, useSignal } from "./hooks";
import { FIELDS, MATCH_MODE_LABELS, wordPattern, type MatchField, type RuleValue } from "./ruleFields";

const IMPACT_DELAY_MS = 300;
/** The id of the unsaved rule being added; saved rules have positive ids. */
const NEW_RULE_ID = -1;
const SHORT_FIELD_LABELS: Record<MatchField, string> = { merchant: "Merch.", description: "Desc.", memo: "Memo" };

const valueOf = (rule: CategoryRule): RuleValue => ({
  category_id: rule.category_id,
  match_text: rule.match_text,
  match_fields: rule.match_fields,
  match_mode: rule.match_mode,
  flow_type: rule.flow_type,
});
const sameValue = (left: RuleValue, right: RuleValue) => JSON.stringify(left) === JSON.stringify(right);
/** Columns the rule list sorts by; sorting only changes the view, never the priority order. */
type SortKey = "position" | "text" | "category";
type Sort = { key: SortKey; descending: boolean };
const PRIORITY_ORDER: Sort = { key: "position", descending: false };

/** A column heading that sorts the list by that column, and flips the direction when sorted by it already. */
function SortHeader({
  label,
  column,
  sort,
  onSort,
  colSpan,
}: {
  label: string;
  column: SortKey;
  sort: Sort;
  onSort: (sort: Sort) => void;
  colSpan?: number;
}) {
  const active = sort.key === column;
  return (
    <th colSpan={colSpan} aria-sort={active ? (sort.descending ? "descending" : "ascending") : "none"}>
      <button
        className={active ? "sort-header active" : "sort-header"}
        onClick={() => onSort({ key: column, descending: active && !sort.descending })}
      >
        {label}<span className="sort-arrow" aria-hidden="true">{active ? (sort.descending ? "↓" : "↑") : "↕"}</span>
      </button>
    </th>
  );
}

const plural = (count: number, word: string, words = `${word}s`) => `${count} ${count === 1 ? word : words}`;

/** Which rules the health filter lists: all, any flagged rule, or those with one signal. */
type HealthFilter = "all" | "attention" | RuleHealth;
const HEALTH_FILTERS: [HealthFilter, string][] = [
  ["all", "All rules"],
  ["attention", "Needs attention"],
  ["idle", "Idle"],
  ["shadowed", "Shadowed"],
  ["broad", "Broad"],
];
const healthOf = (rule: CategoryRule): RuleHealth[] => rule.health ?? [];
const joinWords = (items: string[]) => items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;

/** "#9 “Marie”": an earlier rule as the list shows it. */
const ruleLabel = (position: number | undefined, text: string) => `${position === undefined ? "rule" : `rule #${position}`} “${text}”`;

/** Plain words for one health signal; `positionOf` gives a rule's number in the priority order. */
function healthText(rule: CategoryRule, code: RuleHealth, positionOf: (id: number) => number | undefined) {
  if (code === "idle") {
    return rule.match_count === 0
      ? "Matches no transactions."
      : `Decides none of its ${plural(rule.match_count, "match", "matches")}: earlier rules take them all.`;
  }
  if (code === "shadowed") {
    const count = rule.match_count - rule.first_match_count;
    const by = rule.shadowed_by ?? [];
    const only = by.length === 1 && by[0].count === count ? by[0] : null;
    const heading = `Matches ${plural(count, "transaction")} that `;
    return only
      ? `${heading}${ruleLabel(positionOf(only.id), only.match_text)} decides first.`
      : `${heading}earlier rules decide first${by.length ? `, mostly ${joinWords(by.map((item) =>
        `${ruleLabel(positionOf(item.id), item.match_text)} (${item.count})`))}` : ""}.`;
  }
  return `A contains rule on only “${rule.match_text.trim()}”: it may match more than intended.`;
}

/** `text` with the parts a rule matches marked. */
function Highlighted({ text, needle, mode }: { text: string; needle: string; mode: MatchMode }) {
  const query = needle.trim().toLowerCase();
  if (!text) return <span className="muted">—</span>;
  if (!query) return <>{text}</>;
  if (mode === "exact") {
    return text.trim().toLowerCase().split(/\s+/).join(" ") === query.split(/\s+/).join(" ") ? <mark>{text}</mark> : <>{text}</>;
  }
  const parts: React.ReactNode[] = [];
  const lower = text.toLowerCase();
  let start = 0;
  // Each occurrence as [index, length]: any occurrence, or for `word` only the whole-word ones.
  const found: [number, number][] = [];
  if (mode === "word") {
    for (const match of text.matchAll(wordPattern(query))) found.push([match.index, match[0].length]);
  } else {
    for (let index = lower.indexOf(query); index >= 0; index = lower.indexOf(query, index + query.length)) {
      found.push([index, query.length]);
    }
  }
  for (const [index, length] of found) {
    parts.push(text.slice(start, index), <mark key={index}>{text.slice(index, index + length)}</mark>);
    start = index + length;
  }
  parts.push(text.slice(start));
  return <>{parts}</>;
}

/** A transaction in the Impact pane: its date, amount, and texts with the rule's matches marked. */
function ImpactCard({
  transaction,
  value,
  className,
  children,
}: {
  transaction: { date: string; amount: string } & Record<MatchField, string>;
  value: RuleValue;
  className: string;
  children: React.ReactNode;
}) {
  return (
    <div className={`impact-match ${className}`.trim()}>
      <div className="impact-match-heading">
        <span>{transaction.date}</span>
        <span className={Number(transaction.amount) < 0 ? "negative" : "positive"}>{money(transaction.amount)}</span>
      </div>
      <dl>
        {FIELDS.map(([field, label]) => (
          <div key={field} className={value.match_fields.includes(field) ? "" : "unused-field"}>
            <dt>{label}</dt>
            <dd><Highlighted text={transaction[field]} needle={value.match_text} mode={value.match_mode} /></dd>
          </div>
        ))}
      </dl>
      <p className="impact-match-category">{children}</p>
    </div>
  );
}

/** One rule as a row of inline controls editing its draft. */
function RuleRow({
  rule,
  value,
  position,
  categories,
  selected,
  edited,
  busy,
  positionOf,
  onSelect,
  onChange,
  drag,
  onDelete,
}: {
  rule: CategoryRule;
  value: RuleValue;
  position: number;
  positionOf: (id: number) => number | undefined;
  categories: { id: number; name: string }[];
  selected: boolean;
  edited: boolean;
  busy: boolean;
  onSelect: () => void;
  onChange: (changes: Partial<RuleValue>) => void;
  /** Dragging a row by its handle reorders rules. */
  drag: {
    /** Dragging needs the list in priority order. */
    enabled: boolean;
    dragging: boolean;
    dropAfter: boolean | null;
    onStart: () => void;
    onOver: (after: boolean) => void;
    onDrop: () => void;
    onEnd: () => void;
  };
  onDelete: () => void;
}) {
  const [armed, setArmed] = useState(false);
  const label = rule.id === NEW_RULE_ID ? "new rule" : `rule ${rule.match_text}`;
  const toggleField = (field: MatchField) => {
    const next = value.match_fields.includes(field)
      ? value.match_fields.filter((item) => item !== field)
      : [...value.match_fields, field];
    if (next.length) onChange({ match_fields: FIELDS.map(([name]) => name).filter((name) => next.includes(name)) });
  };
  return (
    <tr
      data-rule-id={rule.id}
      className={[
        rule.first_match_count === 0 && !edited ? "idle-rule" : "",
        selected ? "selected-rule" : "",
        drag.dragging ? "dragging-rule" : "",
        drag.dropAfter === null ? "" : drag.dropAfter ? "drop-after" : "drop-before",
      ].join(" ").trim()}
      onClick={onSelect}
      onFocus={onSelect}
      draggable={armed}
      onDragStart={(event) => {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", String(rule.id));
        drag.onStart();
      }}
      onDragOver={(event) => {
        event.preventDefault();
        const box = event.currentTarget.getBoundingClientRect();
        drag.onOver(event.clientY > box.top + box.height / 2);
      }}
      onDrop={(event) => {
        event.preventDefault();
        drag.onDrop();
      }}
      onDragEnd={() => {
        setArmed(false);
        drag.onEnd();
      }}
    >
      <td className="rule-drag">
        {drag.enabled && (
          <span
            className="drag-handle"
            title="Drag to change priority"
            aria-hidden="true"
            onMouseDown={() => setArmed(!busy)}
            onMouseUp={() => setArmed(false)}
          >⠿</span>
        )}
      </td>
      <td className="rule-position">
        {edited ? <span className="edited-dot" title="Unsaved changes">●</span> : position}
      </td>
      <td>
        <select
          aria-label={`Match mode of ${label}`}
          value={value.match_mode}
          disabled={busy}
          onChange={(event) => onChange({ match_mode: event.target.value as MatchMode })}
        >
          {(["contains", "word", "exact"] as const).map((mode) => (
            <option key={mode} value={mode}>{MATCH_MODE_LABELS[mode]}</option>
          ))}
        </select>
      </td>
      <td className="rule-text-cell">
        <input
          aria-label={`Text of ${label}`}
          placeholder={rule.id === NEW_RULE_ID ? "Text to match…" : undefined}
          autoFocus={rule.id === NEW_RULE_ID}
          value={value.match_text}
          maxLength={120}
          disabled={busy}
          onChange={(event) => onChange({ match_text: event.target.value })}
        />
        {(healthOf(rule).length > 0 || (rule.itemized_count ?? 0) > 0) && (
          <span className="health-tags">
            {healthOf(rule).map((code) => (
              <span className="health-tag" key={code} title={healthText(rule, code, positionOf)}>{code}</span>
            ))}
            {(rule.itemized_count ?? 0) > 0 && (
              <span className="health-tag" title={`${rule.itemized_count} of ${rule.match_count} matches are card payments replaced by their statement's purchases; they keep this category but don't count in totals`}>{rule.itemized_count} itemized</span>
            )}
          </span>
        )}
      </td>
      <td className="rule-field-toggles">
        {FIELDS.map(([field, label]) => (
          <button
            key={field}
            className={value.match_fields.includes(field) ? "field-toggle on" : "field-toggle"}
            aria-pressed={value.match_fields.includes(field)}
            title={label}
            disabled={busy}
            onClick={() => toggleField(field)}
          >{SHORT_FIELD_LABELS[field]}</button>
        ))}
      </td>
      <td>
        <select
          aria-label={`Category of ${label}`}
          value={value.category_id}
          disabled={busy}
          onChange={(event) => onChange({ category_id: Number(event.target.value) })}
        >
          {categories.map((category) => <option value={category.id} key={category.id}>{category.name}</option>)}
        </select>
      </td>
      <td>
        <select
          aria-label={`Flow of ${label}`}
          value={value.flow_type}
          disabled={busy}
          onChange={(event) => onChange({ flow_type: event.target.value as FlowType })}
        >
          <option value="expense">expense</option>
          <option value="income">income</option>
          <option value="transfer">transfer</option>
        </select>
      </td>
      <td className="numeric" title={`Decides ${rule.first_match_count} of ${rule.match_count} matching transactions`}>
        {rule.id === NEW_RULE_ID ? <span className="muted">—</span> : (
          <>{rule.first_match_count}<span className="muted"> / {rule.match_count}</span></>
        )}
      </td>
      <td className="rules-actions">
        <button
          className="icon-button delete-rule"
          aria-label={`Delete ${label}`}
          title="Delete rule"
          disabled={busy}
          onClick={(event) => {
            event.stopPropagation();
            onDelete();
          }}
        >×</button>
      </td>
    </tr>
  );
}

/** The notice after applying a rule: what it categorized, and what it no longer decides. */
function applyNotice(verb: string, matchText: string, { updated, released }: { updated: number; released: number }) {
  return `${verb} “${matchText}” to ${plural(updated, "transaction")}`
    + (released > 0 ? `; recategorized ${released} it no longer decides.` : ".");
}

/** What shadowing means for a rule, with its earlier rules' numbers selecting those rules. */
function ShadowedNote({
  rule,
  positionOf,
  onSelectRule,
}: {
  rule: CategoryRule;
  positionOf: (id: number) => number | undefined;
  onSelectRule: (id: number) => void;
}) {
  const by = rule.shadowed_by ?? [];
  return (
    <>
      Matches {plural(rule.match_count - rule.first_match_count, "transaction")} that earlier rules decide first
      {by.length > 0 && <>: {by.map((item, index) => (
        <span key={item.id}>
          {index > 0 && ", "}
          <button className="rule-link" title={`Select this rule`} onClick={() => onSelectRule(item.id)}>
            {positionOf(item.id) === undefined ? "rule" : `#${positionOf(item.id)}`}
          </button>
          {" "}“{item.match_text}” ({item.count})
        </span>
      ))}</>}.
    </>
  );
}

/** The live effect of the selected rule's draft, with the actions that commit it. */
function ImpactPane({
  accountType,
  rule,
  value,
  edited,
  busy,
  positionOf,
  onSelectRule,
  onChange,
  onSave,
  onApply,
  onDiscard,
}: {
  accountType: AccountType;
  rule: CategoryRule;
  value: RuleValue;
  edited: boolean;
  busy: boolean;
  positionOf: (id: number) => number | undefined;
  /** Selects another rule, such as an earlier one that decides this rule's matches first. */
  onSelectRule: (id: number) => void;
  onChange: (changes: Partial<RuleValue>) => void;
  onSave: (apply: boolean) => void;
  onApply: () => void;
  onDiscard: () => void;
}) {
  const [impact, setImpact] = useState<RuleImpact | null>(null);
  const [error, setError] = useState("");
  const request = JSON.stringify(value);

  useEffect(() => {
    const draft = JSON.parse(request) as RuleValue;
    if (!draft.match_text.trim()) {
      setImpact(null);
      setError("Enter text to match.");
      return;
    }
    const timer = setTimeout(() => {
      api<RuleImpact>("/api/category-rules/impact", {
        method: "POST",
        json: {
          account_type: accountType,
          ...(rule.id === NEW_RULE_ID ? {} : { rule_id: rule.id }),
          ...draft,
          match_text: draft.match_text.trim(),
        },
      })
        .then((result) => {
          setImpact(result);
          setError("");
        })
        .catch((cause) => setError(errorMessage(cause, "Could not preview the rule")));
    }, IMPACT_DELAY_MS);
    return () => clearTimeout(timer);
  }, [accountType, request, rule.id]);

  return (
    <aside className="panel impact-pane" aria-label="Rule impact">
      <div className="impact-heading">
        <h3>Impact</h3>
        <div className="impact-actions">
          {edited ? (
            <>
              <button className="button secondary" disabled={busy} onClick={onDiscard}>Discard</button>
              <button className="button secondary" disabled={busy || !value.match_text.trim()} onClick={() => onSave(false)}>Save</button>
              <button className="button primary" disabled={busy || !value.match_text.trim()} onClick={() => onSave(true)}>Save and apply</button>
            </>
          ) : (impact ? impact.change_count + impact.released_count : 0) > 0 && (
            <button className="button primary" disabled={busy} onClick={onApply}>
              Apply{impact ? ` to ${plural(impact.change_count + impact.released_count, "transaction")}` : ""}
            </button>
          )}
        </div>
      </div>
      {rule.id !== NEW_RULE_ID && healthOf(rule).length > 0 && (
        <section className="impact-health" aria-label="Rule health">
          <h4 className="impact-subheading">Health{edited && " · of the saved rule"}</h4>
          <ul>
            {healthOf(rule).map((code) => (
              <li key={code}>
                <span className="health-tag">{code}</span>
                {code === "shadowed" ? (
                  <ShadowedNote rule={rule} positionOf={positionOf} onSelectRule={onSelectRule} />
                ) : healthText(rule, code, positionOf)}
              </li>
            ))}
          </ul>
        </section>
      )}
      {error && <p className="muted">{error}</p>}
      {impact && (
        <>
          <p className="impact-summary">
            <strong>{plural(impact.match_count, "match", "matches")}</strong>
            {(impact.itemized_count ?? 0) > 0 && (
              <span title={"Card settlements replaced by their statement's purchases; they don't count in totals"}>{" · "}{impact.itemized_count} itemized</span>
            )}
            {" · "}{impact.win_count} decided by this rule
            {" · "}<strong className={impact.change_count ? "impact-changes" : ""}>{impact.change_count} would change</strong>
            {impact.released_count > 0 && <>{" · "}<strong className="impact-released">{impact.released_count} no longer decided</strong></>}
          </p>
          <p className="impact-fields">
            Text found in
            {FIELDS.map(([field, label]) => (
              <button
                key={field}
                className={value.match_fields.includes(field) ? "field-toggle on" : "field-toggle"}
                aria-pressed={value.match_fields.includes(field)}
                title={`Match ${label.toLowerCase()}`}
                disabled={busy || (value.match_fields.length === 1 && value.match_fields[0] === field)}
                onClick={() => onChange({
                  match_fields: FIELDS.map(([name]) => name).filter((name) =>
                    name === field ? !value.match_fields.includes(field) : value.match_fields.includes(name)),
                })}
              >{label} {impact.field_counts[field]}</button>
            ))}
          </p>
          <div className="impact-matches">
            {impact.released_count > 0 && (
              <>
                <h4 className="impact-subheading">No longer decided · {impact.released_count}</h4>
                <p className="muted small">
                  This rule categorized these before. Applying it moves them to the next matching rule’s category,
                  or to Uncategorized; saving alone keeps them until you apply.
                </p>
                {impact.released.map((transaction) => (
                  <ImpactCard transaction={transaction} value={value} className="released" key={transaction.id}>
                    {transaction.category} → <strong>{transaction.new_category}</strong>
                    {transaction.decided_by ? <> · rule “{transaction.decided_by}”</> : <> · no other rule matches</>}
                  </ImpactCard>
                ))}
                <h4 className="impact-subheading">Matches · {impact.match_count}</h4>
              </>
            )}
            {impact.matches.map((match) => (
              <ImpactCard
                transaction={match}
                value={value}
                className={`${match.decided_by || match.manual ? "claimed" : ""} ${match.changes ? "changes" : ""}`}
                key={match.id}
              >
                {match.decided_by
                  ? <>Stays {match.category} · claimed by the earlier rule “{match.decided_by}”</>
                  : match.manual ? <>Set by hand · stays {match.category}</>
                  : match.changes ? <>{match.category} → <strong>will change</strong></> : <>Already {match.category}</>}
              </ImpactCard>
            ))}
            {impact.truncated && <p className="muted small">Showing the {impact.matches.length} newest matches.</p>}
            {!impact.matches.length && <p className="muted">No transactions match.</p>}
          </div>
        </>
      )}
    </aside>
  );
}

/** Every rule of an account type in priority order, edited inline with a live impact preview. */
export function RulesView({
  accountType,
  initialFilter = "",
  initialNewRuleText = "",
  initialSelectedId,
  newItemSignal = 0,
  onNotice,
  onError,
  onChanged,
  onClose,
}: {
  accountType: AccountType;
  /** Starts the list filtered, such as by a category name. */
  initialFilter?: string;
  /** Starts with a new rule matching this text in every field, such as a transaction search. */
  initialNewRuleText?: string;
  /** Starts with this rule selected and scrolled into view, such as one opened from a transaction. */
  initialSelectedId?: number;
  /** Starts a new rule each time this counter changes, such as from a keyboard shortcut. */
  newItemSignal?: number;
  onNotice: (message: string) => void;
  onError: (message: string) => void;
  onChanged: () => Promise<void>;
  onClose: () => void;
}) {
  const [categories, setCategories] = useState<ManagedCategory[]>([]);
  const [filter, setFilter] = useState(initialFilter);
  const [healthFilter, setHealthFilter] = useState<HealthFilter>("all");
  const [busy, setBusy] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(initialSelectedId ?? null);
  const [drafts, setDrafts] = useState<Record<number, RuleValue>>({});
  const [newRule, setNewRule] = useState<RuleValue | null>(null);
  const [sort, setSort] = useState<Sort>(PRIORITY_ORDER);
  const tableBody = useRef<HTMLTableSectionElement>(null);
  const inPriorityOrder = sort.key === "position" && !sort.descending;

  const load = useCallback(async () => {
    try {
      setCategories(await api<ManagedCategory[]>(`/api/category-management?account_type=${accountType}`));
    } catch (cause) {
      onError(errorMessage(cause, "Could not load rules"));
    }
  }, [accountType, onError]);

  useEffect(() => {
    void load();
  }, [load]);

  const choices = useMemo(
    () => categories.filter((category) => category.name !== "Uncategorized").map(({ id, name }) => ({ id, name })),
    [categories],
  );
  const allRules = useMemo(
    () => categories.flatMap((category) => category.rules).sort((left, right) => left.priority - right.priority),
    [categories],
  );
  const positions = useMemo(() => new Map(allRules.map((rule, index) => [rule.id, index + 1])), [allRules]);
  const positionOf = useCallback((id: number) => positions.get(id), [positions]);
  const healthCounts = useMemo(() => {
    const counts: Record<HealthFilter, number> = { all: allRules.length, attention: 0, idle: 0, shadowed: 0, broad: 0 };
    for (const rule of allRules) {
      if (healthOf(rule).length) counts.attention += 1;
      for (const code of healthOf(rule)) counts[code] += 1;
    }
    return counts;
  }, [allRules]);
  const rules = useMemo(() => {
    const names = new Map(categories.map((category) => [category.id, category.name]));
    const needle = filter.trim().toLowerCase();
    const text = (rule: CategoryRule) => rule.match_text;
    const category = (rule: CategoryRule) => names.get(rule.category_id) ?? "";
    const compare = (left: { rule: CategoryRule; position: number }, right: { rule: CategoryRule; position: number }) => {
      const value = sort.key === "position" ? 0
        : (sort.key === "text" ? text : category)(left.rule).localeCompare((sort.key === "text" ? text : category)(right.rule), undefined, { sensitivity: "base" });
      // Ties keep priority order, so rules of one category stay in the order they apply.
      return (sort.descending ? -1 : 1) * (value || left.position - right.position);
    };
    return allRules
      .map((rule, index) => ({ rule, position: index + 1 }))
      .filter(({ rule }) => healthFilter === "all"
        || (healthFilter === "attention" ? healthOf(rule).length > 0 : healthOf(rule).includes(healthFilter)))
      .filter(({ rule }) => !needle
        || text(rule).toLowerCase().includes(needle)
        || category(rule).toLowerCase().includes(needle))
      .sort(compare);
  }, [allRules, categories, filter, healthFilter, sort]);
  const newRuleRow: CategoryRule | null = newRule && {
    ...newRule,
    id: NEW_RULE_ID,
    legacy_normalized: false,
    priority: Number.MAX_SAFE_INTEGER,
    match_count: 0,
    first_match_count: 0,
    samples: [],
  };
  const selected = (selectedId === NEW_RULE_ID ? newRuleRow : null)
    ?? allRules.find((rule) => rule.id === selectedId) ?? rules[0]?.rule ?? null;
  const valueFor = (rule: CategoryRule) => (rule.id === NEW_RULE_ID && newRule) || drafts[rule.id] || valueOf(rule);

  /** Starts an unsaved rule at the end of the priority order, in the filtered category if the filter names one. */
  const startNewRule = (matchText = "") => {
    const category = categories.find((item) => item.name.toLowerCase() === filter.trim().toLowerCase()
      && item.name !== "Uncategorized") ?? categories.find((item) => item.id === choices[0]?.id);
    if (!category) return;
    setNewRule({
      category_id: category.id,
      match_text: matchText,
      match_fields: matchText ? FIELDS.map(([field]) => field) : ["merchant"],
      match_mode: "contains",
      flow_type: category.rules[0]?.flow_type ?? "expense",
    });
    setSelectedId(NEW_RULE_ID);
  };
  const startedInitialRule = useRef(false);
  useEffect(() => {
    if (!initialNewRuleText.trim() || startedInitialRule.current || !categories.length) return;
    startedInitialRule.current = true;
    startNewRule(initialNewRuleText.trim());
  }, [categories, initialNewRuleText]);
  /** Selects a rule by id and scrolls its row into view, if the list shows it. */
  const selectRule = (id: number) => {
    setSelectedId(id);
    tableBody.current?.querySelector(`[data-rule-id="${id}"]`)?.scrollIntoView?.({ block: "nearest" });
  };
  const discardNewRule = () => {
    setNewRule(null);
    setSelectedId(null);
  };

  const changeDraft = (rule: CategoryRule, changes: Partial<RuleValue>) => {
    setSelectedId(rule.id);
    if (rule.id === NEW_RULE_ID) {
      setNewRule((current) => current && { ...current, ...changes });
      return;
    }
    setDrafts((current) => {
      const next = { ...current, [rule.id]: { ...(current[rule.id] ?? valueOf(rule)), ...changes } };
      if (sameValue(next[rule.id], valueOf(rule))) delete next[rule.id];
      return next;
    });
  };
  const dropDraft = (ruleId: number) => setDrafts((current) => {
    const next = { ...current };
    delete next[ruleId];
    return next;
  });

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

  const applyRule = async (ruleId: number) => {
    const result = await api<{ updated: number; released: number }>(`/api/category-rules/${ruleId}/run`, {
      method: "POST",
      json: { account_type: accountType },
    });
    return result;
  };

  const saveRule = (rule: CategoryRule, apply: boolean) => run("Could not save rule", async () => {
    const value = valueFor(rule);
    const json = { account_type: accountType, ...value, match_text: value.match_text.trim() };
    let ruleId = rule.id;
    if (rule.id === NEW_RULE_ID) {
      ruleId = (await api<{ id: number }>("/api/category-rules", { method: "POST", json })).id;
      setNewRule(null);
      setSelectedId(ruleId);
    } else {
      await api(`/api/category-rules/${rule.id}`, { method: "PUT", json });
      dropDraft(rule.id);
    }
    if (apply) {
      onNotice(applyNotice("Saved and applied", json.match_text, await applyRule(ruleId)));
      await onChanged();
    } else {
      onNotice("Rule saved.");
    }
    await load();
  });

  const applySaved = (rule: CategoryRule) => run("Could not apply rule", async () => {
    onNotice(applyNotice("Applied", rule.match_text, await applyRule(rule.id)));
    await Promise.all([load(), onChanged()]);
  });

  /** Moves a rule just before or after another one in the account-wide priority order. */
  const moveRule = (ruleId: number, targetId: number, after: boolean) => {
    if (ruleId === targetId) return;
    const ids = allRules.map((item) => item.id).filter((id) => id !== ruleId);
    ids.splice(ids.indexOf(targetId) + (after ? 1 : 0), 0, ruleId);
    if (ids.join() === allRules.map((item) => item.id).join()) return;
    void run("Could not reorder rules", async () => {
      await api("/api/category-rules/order", { method: "PUT", json: { account_type: accountType, rule_ids: ids } });
      await Promise.all([load(), onChanged()]);
    });
  };
  const [dragId, setDragId] = useState<number | null>(null);
  const [dropTarget, setDropTarget] = useState<{ id: number; after: boolean } | null>(null);

  // Up and down arrows move the selection through the listed rules, in the order shown.
  useArrowSelection(rules.map(({ rule }) => rule.id), selected?.id, selectRule);
  useSignal(newItemSignal, () => {
    if (!busy && !newRule) startNewRule();
  });

  // A rule opened from elsewhere is scrolled into view once the rules have loaded.
  const scrolledToInitial = useRef(false);
  useEffect(() => {
    if (initialSelectedId === undefined || scrolledToInitial.current || !allRules.length) return;
    scrolledToInitial.current = true;
    tableBody.current?.querySelector(`[data-rule-id="${initialSelectedId}"]`)?.scrollIntoView?.({ block: "center" });
  }, [allRules, initialSelectedId]);

  const deleteRule = (rule: CategoryRule) => {
    if (!window.confirm(`Delete the rule “${rule.match_text}”? The transactions it categorized will be moved to Uncategorized.`)) {
      return;
    }
    void run("Could not delete rule", async () => {
      await api(`/api/category-rules/${rule.id}?account_type=${accountType}`, { method: "DELETE" });
      dropDraft(rule.id);
      await Promise.all([load(), onChanged()]);
    });
  };

  return (
    <div className="rules-view">
      <section className="panel rules-pane" aria-label="Rules">
        <div className="section-heading">
          <div><h3>Rules <span className="count">{allRules.length}</span></h3></div>
          <div className="transaction-filters">
            <button className="button secondary" disabled={busy || !!newRule || !choices.length} onClick={() => startNewRule()}>
              New rule
            </button>
            <select
              aria-label="Filter by health"
              value={healthFilter}
              onChange={(event) => setHealthFilter(event.target.value as HealthFilter)}
            >
              {HEALTH_FILTERS.map(([value, label]) => (
                <option key={value} value={value}>{value === "all" ? label : `${label} (${healthCounts[value]})`}</option>
              ))}
            </select>
            <input
              className="search-input"
              aria-label="Filter rules"
              placeholder="Filter by text or category…"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              onKeyDown={clearOnEscape(() => setFilter(""))}
            />
            <button className="icon-button" aria-label="Close rules" onClick={onClose}>×</button>
          </div>
        </div>
        <p className="muted small">
          Edit a rule in its row and check its impact before saving. The first matching rule wins; faded rules never win.
          {!inPriorityOrder && " Sort by # to drag rules."}
        </p>
        <div className="table-scroll">
          <table className="rules-table">
            <thead>
              <tr>
                <th aria-label="Drag to reorder" />
                <SortHeader label="#" column="position" sort={sort} onSort={setSort} />
                <SortHeader label="Rule" column="text" sort={sort} onSort={setSort} colSpan={2} />
                <th>In</th>
                <SortHeader label="Category" column="category" sort={sort} onSort={setSort} />
                <th>Flow</th>
                <th className="numeric">Wins</th><th aria-label="Actions" />
              </tr>
            </thead>
            <tbody ref={tableBody}>
              {rules.map(({ rule, position }) => (
                <RuleRow
                  key={rule.id}
                  rule={rule}
                  value={valueFor(rule)}
                  position={position}
                  categories={choices}
                  positionOf={positionOf}
                  selected={rule.id === selected?.id}
                  edited={rule.id in drafts}
                  busy={busy}
                  onSelect={() => setSelectedId(rule.id)}
                  onChange={(changes) => changeDraft(rule, changes)}
                  drag={{
                    enabled: inPriorityOrder,
                    dragging: dragId === rule.id,
                    dropAfter: dragId !== null && dragId !== rule.id && dropTarget?.id === rule.id ? dropTarget.after : null,
                    onStart: () => setDragId(rule.id),
                    onOver: (after) => setDropTarget({ id: rule.id, after }),
                    onDrop: () => {
                      if (dragId !== null) moveRule(dragId, rule.id, dropTarget?.id === rule.id && dropTarget.after);
                      setDragId(null);
                      setDropTarget(null);
                    },
                    onEnd: () => {
                      setDragId(null);
                      setDropTarget(null);
                    },
                  }}
                  onDelete={() => deleteRule(rule)}
                />
              ))}
              {newRuleRow && (
                <RuleRow
                  key={NEW_RULE_ID}
                  rule={newRuleRow}
                  value={newRuleRow}
                  position={allRules.length + 1}
                  categories={choices}
                  positionOf={positionOf}
                  selected={selected?.id === NEW_RULE_ID}
                  edited
                  busy={busy}
                  onSelect={() => setSelectedId(NEW_RULE_ID)}
                  onChange={(changes) => changeDraft(newRuleRow, changes)}
                  drag={{
                    enabled: false,
                    dragging: false,
                    dropAfter: null,
                    onStart: () => {},
                    onOver: () => {},
                    onDrop: () => {},
                    onEnd: () => {},
                  }}
                  onDelete={discardNewRule}
                />
              )}
            </tbody>
          </table>
          {!rules.length && !newRuleRow && <p className="muted empty-state">{allRules.length ? "No rules match this filter." : "No rules yet."}</p>}
        </div>
      </section>
      {selected && (
        <ImpactPane
          key={selected.id}
          accountType={accountType}
          rule={selected}
          value={valueFor(selected)}
          edited={selected.id === NEW_RULE_ID || selected.id in drafts}
          busy={busy}
          positionOf={positionOf}
          onSelectRule={selectRule}
          onChange={(changes) => changeDraft(selected, changes)}
          onSave={(apply) => void saveRule(selected, apply)}
          onApply={() => void applySaved(selected)}
          onDiscard={() => (selected.id === NEW_RULE_ID ? discardNewRule() : dropDraft(selected.id))}
        />
      )}
    </div>
  );
}
