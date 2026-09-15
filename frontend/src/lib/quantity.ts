/** A quick in-page check of a typed quantity, so the cell says what is wrong
 * before the save does. The server's parser (backend/app/extraction/values.py)
 * is the authority; this mirrors its main rules and never changes the text. */

const WORDS = new Set(["lot", "set", "nos", "no", "pcs", "pc", "ea", "each", "item", "sum"]);
const UNITS = new Set(["nos", "no", "pcs", "pc", "ea", "each", "set", "sets", "m", "mtr", "mtrs", "lm", "roll", "rolls", "lot"]);

/** Why a quantity would be refused, or null when it is acceptable. */
export function quantityProblem(raw: string | null | undefined): string | null {
  const text = (raw ?? "").trim();
  if (!text) return null;
  if (/^[\s|_([]*[-−–—]\s*\d/.test(text)) return "A count cannot be negative";
  const stripped = text.replace(/^[\s|_—–:;.,()[\]{}'"`~]+|[\s|_—–:;.,()[\]{}'"`~]+$/g, "");
  if (/\d\s*[x×X*]\s*\d/.test(stripped)) return "'a x b' is not one count: enter the total";
  if (/^\d+$/.test(stripped)) return Number(stripped) > 999_999 ? "Too large for one line" : null;
  if (/^\d{1,3}(,\d{3})+$/.test(stripped)) return null;
  if (/^\d+[.,]\d+$/.test(stripped)) return "An equipment count is a whole number";
  if (/^\d{1,3}( \d{3})+$/.test(stripped)) return "Remove the space, or enter two lines";
  const withUnit = /^(\d+)\s*([A-Za-z]+)\.?$/.exec(stripped);
  if (withUnit && UNITS.has(withUnit[2].toLowerCase())) return null;
  if (/^[A-Za-z]+$/.test(stripped) && WORDS.has(stripped.toLowerCase())) return null;
  return "Enter a whole number or Lot / Set";
}
