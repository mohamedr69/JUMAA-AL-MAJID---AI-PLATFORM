import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { api } from "../lib/api";
import type { ArchiveSuggestion } from "../lib/types";

/** How long to wait after the last keystroke before asking the server.
 * Long enough that typing an EP number out is one request rather than
 * five, short enough that the list is there by the time the eye moves. */
const DEBOUNCE_MS = 180;

/** Below this, everything matches and the list is noise. Mirrors the
 * minimum the backend's `search` applies. */
const MIN_QUERY = 2;

interface Props {
  value: string;
  onChange: (value: string) => void;
  /** A suggestion was chosen -- with the mouse, or with Enter while it was
   * highlighted. The EP number is already set by then. */
  onPick: (suggestion: ArchiveSuggestion) => void;
  /** Enter pressed with nothing highlighted: the parent's own submit. */
  onSubmit: () => void;
  disabled?: boolean;
  id?: string;
}

/**
 * The EP number box, with the archive's own project list behind it.
 *
 * The archive's EP folders are indexed in the database
 * (backend app/services/ep_directory.py), so typing "294" suggests
 * "EP-29495 — IVY Garden 2" instead of the engineer having to know the
 * number by heart and the server walking a synced OneDrive drive for
 * every attempt. A name works too: "wasl" finds EP-30851.
 *
 * Suggestions are a help, never a gate. An EP number typed in full is
 * submitted whatever the list says -- a folder created since the last
 * scan is not in the index yet, and the server falls back to searching
 * the archive for it (and records it, so the next person does not wait).
 */
export function EpNumberSearch({ value, onChange, onPick, onSubmit, disabled, id }: Props) {
  const listId = useId();
  const [suggestions, setSuggestions] = useState<ArchiveSuggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(-1);
  const [loading, setLoading] = useState(false);
  // What the last suggestion was picked for: a pick sets the input to the
  // EP number, which would otherwise fetch again and reopen the list.
  const picked = useRef<string | null>(null);
  const wrapper = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const query = value.trim();
    if (picked.current === query) return;
    if (query.length < MIN_QUERY) {
      setSuggestions([]);
      setOpen(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    const timer = window.setTimeout(() => {
      api
        .get<ArchiveSuggestion[]>(`/archive/search?q=${encodeURIComponent(query)}`)
        .then((rows) => {
          if (cancelled) return;
          setSuggestions(rows);
          setOpen(rows.length > 0);
          setHighlighted(-1);
        })
        // A search box that cannot reach the index simply suggests
        // nothing; the number can still be typed out and submitted.
        .catch(() => {
          if (!cancelled) setSuggestions([]);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      setLoading(false);
    };
  }, [value]);

  useEffect(() => {
    function onClickAway(event: MouseEvent) {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickAway);
    return () => document.removeEventListener("mousedown", onClickAway);
  }, []);

  function choose(suggestion: ArchiveSuggestion) {
    picked.current = suggestion.ep_number;
    onChange(suggestion.ep_number);
    setOpen(false);
    setHighlighted(-1);
    onPick(suggestion);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      setOpen(false);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (!open && suggestions.length > 0) {
        setOpen(true);
        return;
      }
      if (suggestions.length === 0) return;
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      // -1 is "nothing highlighted": what was typed stands, and Enter
      // submits it. Arrowing past either end comes back to it.
      setHighlighted((current) => {
        const next = current + step;
        if (next < -1) return suggestions.length - 1;
        if (next >= suggestions.length) return -1;
        return next;
      });
      return;
    }
    if (event.key === "Enter") {
      // Enter on a highlighted row picks it; Enter with nothing
      // highlighted is "I have typed the number, go".
      if (open && highlighted >= 0 && highlighted < suggestions.length) {
        event.preventDefault();
        choose(suggestions[highlighted]);
        return;
      }
      event.preventDefault();
      setOpen(false);
      onSubmit();
    }
  }

  return (
    <div ref={wrapper} className="relative">
      <input
        id={id}
        required
        value={value}
        onChange={(e) => {
          picked.current = null;
          onChange(e.target.value);
        }}
        onFocus={() => suggestions.length > 0 && setOpen(true)}
        onKeyDown={handleKeyDown}
        placeholder="e.g. 29495 or a project name"
        disabled={disabled}
        autoComplete="off"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={highlighted >= 0 ? `${listId}-${highlighted}` : undefined}
        className="input mt-1"
      />

      {loading && (
        <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400">Searching…</span>
      )}

      {open && suggestions.length > 0 && (
        <ul
          id={listId}
          role="listbox"
          aria-label="Projects in the archive"
          className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
        >
          {suggestions.map((s, index) => (
            <li key={s.ep_number} id={`${listId}-${index}`} role="option" aria-selected={index === highlighted}>
              <button
                type="button"
                // The blur that a click would cause closes the list before
                // the click lands, so the pick is made on mouse-down.
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => choose(s)}
                onMouseEnter={() => setHighlighted(index)}
                className={`block w-full px-3 py-2 text-left text-sm ${
                  index === highlighted ? "bg-brand-50" : "hover:bg-gray-50"
                }`}
              >
                <span className="font-semibold text-navy-900">EP-{s.ep_number}</span>
                {s.project_name && <span className="text-navy-900"> — {s.project_name}</span>}
                <span className="mt-0.5 block truncate text-xs text-gray-400">{s.relative_path}</span>
                <span className="mt-0.5 flex gap-2 text-xs">
                  {s.project_id !== null && (
                    <span className="rounded-full bg-green-50 px-2 py-0.5 font-medium text-green-700">
                      Already in the platform
                    </span>
                  )}
                  {s.locations > 1 && (
                    <span className="rounded-full bg-amber-50 px-2 py-0.5 font-medium text-amber-700">
                      {s.locations} folders
                    </span>
                  )}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
