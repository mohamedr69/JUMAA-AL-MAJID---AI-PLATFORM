/** The API sends naive UTC timestamps; show them in local time. */
export function formatApiDate(value: string | null | undefined, style: "short" | "medium" = "medium"): string {
  if (!value) return "—";
  const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { dateStyle: style, timeStyle: "short" });
}

/** A long archive path, readable: the last `keep` parts, with the full path
 * kept for a tooltip or a copy button. */
export function shortPath(path: string | null | undefined, keep = 2): string {
  if (!path) return "—";
  const parts = path.split(/[\\/]+/).filter(Boolean);
  return parts.length <= keep ? parts.join(" / ") : `… / ${parts.slice(-keep).join(" / ")}`;
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
