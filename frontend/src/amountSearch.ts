/**
 * Whether a transaction search is only an amount or a range ("41,50", "-41", "40..50"), which
 * the backend matches against amounts, so a rule built from the search text could not match it.
 * Mirrors the number notation of the backend's search parser.
 */
const NUMBER = String.raw`\d+(?:[.,]\d+)*`;
const AMOUNT = new RegExp(`^[+-]?(${NUMBER})$`);
const RANGE = new RegExp(String.raw`^[+-]?(${NUMBER})\s*(?:\.\.|-|–)\s*[+-]?(${NUMBER})$`);

function isNumber(text: string): boolean {
  if (!new RegExp(`^${NUMBER}$`).test(text)) return false;
  const groups = text.split(/[.,]/);
  const separators = text.match(/[.,]/g) ?? [];
  let decimal = "";
  if (new Set(separators).size === 2) {
    decimal = groups.pop() as string;
    if (text.slice(0, -decimal.length - 1).includes(separators[separators.length - 1])) return false;
  } else if (separators.length === 1 && groups[1].length !== 3) {
    decimal = groups.pop() as string;
  }
  if (decimal && decimal.length > 2) return false;
  return groups.length < 2 || (groups[0].length <= 3 && !groups[0].startsWith("0") && groups.slice(1).every((group) => group.length === 3));
}

export function isAmountQuery(search: string): boolean {
  const query = search.split(/\s+/).filter((word) => word && word !== "€").join(" ").replace(/€/g, "");
  const range = RANGE.exec(query);
  if (range) return isNumber(range[1]) && isNumber(range[2]);
  const amount = AMOUNT.exec(query);
  // A bare whole number ("4411") also matches text, such as a reference a rule can match, so it is not amount-only.
  return amount !== null && !query.includes(" ") && isNumber(amount[1]) && (search.includes("€") || !/^\d+$/.test(query));
}
