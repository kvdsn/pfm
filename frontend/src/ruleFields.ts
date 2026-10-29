import type { CategoryRule, FlowType, MatchMode } from "./types";

export type MatchField = CategoryRule["match_fields"][number];

/** The editable parts of a rule. */
export type RuleValue = {
  category_id: number;
  match_text: string;
  match_fields: MatchField[];
  match_mode: MatchMode;
  flow_type: FlowType;
};

export const FIELDS: [MatchField, string][] = [
  ["merchant", "Merchant"],
  ["description", "Description"],
  ["memo", "Memo"],
];

/** How each match mode reads in the interface. */
export const MATCH_MODE_LABELS: Record<MatchMode, string> = {
  contains: "contains",
  word: "word",
  exact: "is",
};

/**
 * The whole-word occurrences of a rule's text, the way the backend matches the `word` mode: no letter or
 * digit directly before or after, whitespace runs equal, case ignored. Global, so use it with matchAll.
 */
export function wordPattern(text: string): RegExp {
  const escaped = text.trim().split(/\s+/).map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s+");
  return new RegExp(`(?<![\\p{L}\\p{N}])${escaped}(?![\\p{L}\\p{N}])`, "giu");
}
