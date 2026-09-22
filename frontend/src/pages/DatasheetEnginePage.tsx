import { useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { datasheetHref } from "../components/DatasheetFileList";
import { ApiError, api, apiUrl } from "../lib/api";
import {
  PROJECT_EDITOR_ROLES,
  type DatasheetFile,
  type DatasheetLibrarySummary,
  type DatasheetSuggestion,
  type DatasheetSystem,
} from "../lib/types";

/** The datasheet library, browsed from outside any project.
 *
 * By system first, because that is what an engineer is designing: a fire
 * alarm detector, an exit sign, a fire-rated cable. Each system names the
 * manufacturers that supply it, and one of those opens the brand's sheets.
 *
 * The suggestions come from `/datasheets/index`, which is the library's
 * files *and* the part numbers recorded against them. Both are needed --
 * a part whose sheet is named for it has no recorded link, and a variant
 * on another model's sheet is only ever found through one -- so a dropdown
 * built from either alone would quietly under-report what is there.
 *
 * Every column here is something the library actually knows. It holds no
 * per-file description, no tags and no uploader: it is a synced folder of
 * PDFs, and a file arrives in it without anyone pressing a button. What
 * stands in for a description is the parts recorded against the sheet,
 * which is what an engineer is looking for anyway.
 */
export function DatasheetEnginePage() {
  const { user } = useAuth();
  const [systems, setSystems] = useState<DatasheetSystem[] | null>(null);
  const [libraries, setLibraries] = useState<DatasheetLibrarySummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reindexing, setReindexing] = useState(false);
  /** What is open: nothing (the systems), one brand, or a whole system's
   * brands at once ("Browse all Fire Alarm datasheets"). */
  const [open, setOpen] = useState<{ title: string; brands: string[] } | null>(null);

  const canReindex = !!user && PROJECT_EDITOR_ROLES.includes(user.role);

  function load() {
    return Promise.all([
      api.get<DatasheetSystem[]>("/design-rules/datasheet-systems"),
      api.get<DatasheetLibrarySummary[]>("/design-rules/datasheet-libraries"),
    ])
      .then(([rows, libs]) => {
        setSystems(rows);
        setLibraries(libs);
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : "The datasheet library could not be read"));
  }

  useEffect(() => {
    load();
  }, []);

  async function reindex() {
    setReindexing(true);
    setError(null);
    try {
      await api.post("/design-rules/datasheet-libraries/reindex", {});
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The libraries could not be re-read");
    } finally {
      setReindexing(false);
    }
  }

  return (
    <div className="mx-auto max-w-6xl">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-brand-50 text-brand-600">
            <SheetIcon />
          </span>
          <div>
            <h1 className="text-2xl font-bold text-navy-900">Datasheets</h1>
            <p className="mt-0.5 text-sm text-gray-500">
              The company datasheet library, by system and manufacturer. Shared by every project and read-only here.
            </p>
          </div>
        </div>
        {/* The library is a synced folder: a sheet is added by filing it
         * there, not by uploading it, so the only action that belongs here
         * is re-reading what the sync has since brought in. */}
        {canReindex && (
          <button
            type="button"
            onClick={reindex}
            disabled={reindexing}
            className="rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50 disabled:opacity-60"
          >
            {reindexing ? "Re-reading..." : "Re-read library"}
          </button>
        )}
      </header>

      {error && <div className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      {systems === null && !error && <div className="mt-6 text-sm text-gray-400">Reading the library...</div>}

      {systems !== null && open === null && (
        <SystemCards systems={systems} onOpen={(title, brands) => setOpen({ title, brands })} />
      )}

      {open !== null && (
        <>
          <button
            type="button"
            onClick={() => setOpen(null)}
            className="mt-5 inline-block text-sm font-semibold text-brand-600 hover:underline"
          >
            ← All systems
          </button>
          <BrandWorkbench
            title={open.title}
            brands={open.brands}
            libraries={(libraries ?? []).filter((l) => l.available)}
            onBrand={(name) => setOpen({ title: name, brands: [name] })}
          />
        </>
      )}
    </div>
  );
}

/* --- the systems, each with the brands that supply it ---------------------- */

/** Per system, so each card reads as its own thing rather than four
 * identical grey boxes. Only the tint carries no information: everything
 * a reader needs is written out. */
const SYSTEM_TINTS: Record<string, { tile: string; ring: string; count: string }> = {
  FAS: { tile: "bg-red-50 text-red-600", ring: "border-red-100", count: "bg-red-50 text-red-700" },
  ELS_SELF_CONTAINED: {
    tile: "bg-emerald-50 text-emerald-600",
    ring: "border-emerald-100",
    count: "bg-emerald-50 text-emerald-700",
  },
  ELS_CENTRAL_BATTERY: {
    tile: "bg-violet-50 text-violet-600",
    ring: "border-violet-100",
    count: "bg-violet-50 text-violet-700",
  },
  FRC: { tile: "bg-amber-50 text-amber-600", ring: "border-amber-100", count: "bg-amber-50 text-amber-700" },
};

function SystemCards({
  systems,
  onOpen,
}: {
  systems: DatasheetSystem[];
  onOpen: (title: string, brands: string[]) => void;
}) {
  return (
    <div className="mt-5 grid gap-4 lg:grid-cols-2">
      {systems.map((system) => {
        const tint = SYSTEM_TINTS[system.code] ?? SYSTEM_TINTS.FAS;
        const stocked = system.manufacturers.filter((m) => m.available);
        return (
          <section key={system.code} className={`overflow-hidden rounded-xl border bg-white ${tint.ring}`}>
            <div className="flex items-start gap-3 p-4">
              <span className={`grid h-11 w-11 shrink-0 place-items-center rounded-xl ${tint.tile}`}>
                <SystemIcon code={system.code} />
              </span>
              <div className="min-w-0 flex-1">
                <h2 className="font-bold text-navy-900">{system.label}</h2>
                <p className="mt-0.5 text-xs text-gray-500">{system.description}</p>
              </div>
              <span className={`shrink-0 rounded-xl px-3 py-1.5 text-center ${tint.count}`}>
                <span className="block text-lg font-bold leading-none">{system.datasheets}</span>
                <span className="block text-[10px] font-medium">datasheets</span>
              </span>
            </div>

            {/* Only the brands whose sheets the library actually holds. A
             * supplier with nothing filed is kept in SYSTEM_LIBRARIES, so it
             * appears here the day its folder does, but an unopenable row
             * is not worth the line. */}
            <ul className="border-t border-gray-100">
              {stocked.length === 0 ? (
                <li className="px-4 py-2.5 text-sm text-gray-400">Nothing filed for this system yet.</li>
              ) : (
                stocked.map((brand) => (
                  <li key={brand.name}>
                    <button
                      type="button"
                      onClick={() => onOpen(brand.name, [brand.name])}
                      className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm hover:bg-gray-50"
                    >
                      <span className="min-w-0 flex-1 truncate font-semibold text-navy-900">{brand.name}</span>
                      <span className="shrink-0 text-xs text-gray-500">{brand.datasheets}</span>
                      <span className="text-gray-400">&rsaquo;</span>
                    </button>
                  </li>
                ))
              )}
            </ul>

            <button
              type="button"
              disabled={stocked.length === 0}
              onClick={() =>
                onOpen(
                  system.label,
                  stocked.map((m) => m.name),
                )
              }
              className="flex w-full items-center gap-2 border-t border-gray-100 bg-gray-50/60 px-4 py-3 text-sm font-semibold text-brand-600 hover:bg-brand-50/60 disabled:text-gray-400 disabled:hover:bg-gray-50/60"
            >
              <SheetIcon small />
              Browse all {system.label} datasheets
              <span className="flex-1" />
              <span>&rsaquo;</span>
            </button>
          </section>
        );
      })}
    </div>
  );
}

/* --- the filter bar and everything under it -------------------------------- */

type SortKey = "name" | "updated" | "size" | "pages";

const SORTS: { value: SortKey; label: string }[] = [
  { value: "name", label: "Document (A → Z)" },
  { value: "updated", label: "Recently updated" },
  { value: "size", label: "Largest first" },
  { value: "pages", label: "Most pages" },
];

function BrandWorkbench({
  title,
  brands,
  libraries,
  onBrand,
}: {
  title: string;
  brands: string[];
  libraries: DatasheetLibrarySummary[];
  onBrand: (name: string) => void;
}) {
  const [files, setFiles] = useState<DatasheetFile[] | null>(null);
  const [index, setIndex] = useState<DatasheetSuggestion[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [category, setCategory] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey>("name");
  const [grid, setGrid] = useState(false);
  const [selected, setSelected] = useState<DatasheetFile | null>(null);

  // A system can be supplied by more than one brand, and a library is one
  // brand's folder, so the sheets are fetched per brand and put together.
  const key = brands.join("|");
  useEffect(() => {
    let live = true;
    setFiles(null);
    setIndex(null);
    setSelected(null);
    setCategory("");
    setQuery("");
    Promise.all([
      Promise.all(
        brands.map((b) => api.get<DatasheetFile[]>(`/design-rules/datasheets/all?manufacturer=${encodeURIComponent(b)}`)),
      ),
      Promise.all(
        brands.map((b) =>
          api.get<DatasheetSuggestion[]>(`/design-rules/datasheets/index?manufacturer=${encodeURIComponent(b)}`),
        ),
      ),
    ])
      .then(([listings, indexes]) => {
        if (!live) return;
        const listing = listings.flat();
        setFiles(listing);
        setIndex(indexes.flat());
        setSelected(listing[0] ?? null);
      })
      .catch((err) => live && setError(err instanceof ApiError ? err.message : "The library could not be read"));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  /** Which parts each sheet documents, by path. This is what stands in for
   * a description: the library holds none, and the parts are what the
   * engineer came looking for. */
  const partsByPath = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const row of index ?? []) {
      if (row.kind !== "part") continue;
      const parts = map.get(row.path);
      if (parts) parts.push(row.label);
      else map.set(row.path, [row.label]);
    }
    return map;
  }, [index]);

  const categories = useMemo(() => {
    const seen = new Set<string>();
    for (const file of files ?? []) seen.add(topFolder(file.folder));
    return [...seen].sort();
  }, [files]);

  const shown = useMemo(() => {
    const needle = query.trim().toUpperCase();
    let rows = (files ?? []).filter((f) => !category || topFolder(f.folder) === category);
    if (needle) {
      rows = rows.filter(
        (f) =>
          f.filename.toUpperCase().includes(needle) ||
          f.folder.toUpperCase().includes(needle) ||
          (f.reference_no ?? "").toUpperCase().includes(needle) ||
          (f.document_no ?? "").toUpperCase().includes(needle) ||
          (partsByPath.get(f.path) ?? []).some((part) => part.toUpperCase().includes(needle)),
      );
    }
    const sorted = [...rows];
    sorted.sort((a, b) => {
      if (sort === "updated") return b.modified - a.modified;
      if (sort === "size") return b.size - a.size;
      if (sort === "pages") return b.pages - a.pages;
      return sheetName(a).localeCompare(sheetName(b));
    });
    return sorted;
  }, [files, category, query, sort, partsByPath]);

  const single = brands.length === 1 ? brands[0] : "";

  return (
    <>
      <h2 className="mt-2 text-lg font-bold text-navy-900">{title}</h2>

      <section className="mt-3 rounded-xl border border-gray-200 bg-white px-4 py-3">
        <div className="flex flex-wrap items-end gap-4">
          <label className="min-w-[10rem] flex-1">
            <span className="block text-xs font-semibold text-gray-500">Manufacturer</span>
            <select
              value={single}
              onChange={(event) => onBrand(event.target.value)}
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none"
            >
              {!single && <option value="">All of {title}</option>}
              {libraries.map((l) => (
                <option key={l.name} value={l.name}>
                  {l.name}
                </option>
              ))}
            </select>
          </label>

          <label className="min-w-[10rem] flex-1">
            <span className="block text-xs font-semibold text-gray-500">Category</span>
            <select
              value={category}
              onChange={(event) => setCategory(event.target.value)}
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none"
            >
              <option value="">All categories</option>
              {categories.map((name) => (
                <option key={name} value={name}>
                  {name || "Library root"}
                </option>
              ))}
            </select>
          </label>

          <div className="min-w-[16rem] flex-[2]">
            <span className="block text-xs font-semibold text-gray-500">Search</span>
            <SearchBox
              index={index}
              query={query}
              onQuery={setQuery}
              onPick={(suggestion) => {
                const file = (files ?? []).find((f) => f.path === suggestion.path);
                if (file) {
                  setSelected(file);
                  setCategory("");
                }
                setQuery(suggestion.label);
              }}
            />
          </div>

          {(category || query) && (
            <button
              type="button"
              onClick={() => {
                setCategory("");
                setQuery("");
              }}
              className="pb-2 text-sm font-semibold text-brand-600 hover:underline"
            >
              Clear filters
            </button>
          )}
        </div>
      </section>

      {error && <div className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      {files === null && !error && <div className="mt-6 text-sm text-gray-400">Reading the library...</div>}

      {files !== null && (
        <>
          {selected && <FeaturedCard file={selected} parts={partsByPath.get(selected.path) ?? []} />}

          <section className="mt-4 rounded-xl border border-gray-200 bg-white">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-4 py-3">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                {title} datasheets ({shown.length}
                {shown.length !== files.length ? ` of ${files.length}` : ""})
              </h3>
              <div className="flex items-center gap-3">
                <label className="flex items-center gap-2 text-xs text-gray-500">
                  Sort by
                  <select
                    value={sort}
                    onChange={(event) => setSort(event.target.value as SortKey)}
                    className="rounded-lg border border-gray-300 px-2 py-1.5 text-sm focus:border-brand-500 focus:outline-none"
                  >
                    {SORTS.map((s) => (
                      <option key={s.value} value={s.value}>
                        {s.label}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="flex overflow-hidden rounded-lg border border-gray-300">
                  <ViewToggle active={!grid} onClick={() => setGrid(false)} label="List view">
                    <ListIcon />
                  </ViewToggle>
                  <ViewToggle active={grid} onClick={() => setGrid(true)} label="Grid view">
                    <GridIcon />
                  </ViewToggle>
                </div>
              </div>
            </div>

            {shown.length === 0 ? (
              <p className="px-4 py-6 text-sm text-gray-500">Nothing here matches that.</p>
            ) : grid ? (
              <GridView files={shown} partsByPath={partsByPath} selected={selected} onSelect={setSelected} />
            ) : (
              <ListView files={shown} partsByPath={partsByPath} selected={selected} onSelect={setSelected} />
            )}
          </section>
        </>
      )}
    </>
  );
}

/* --- the search box, with the suggestion dropdown -------------------------- */

const MAX_SUGGESTIONS = 8;

function SearchBox({
  index,
  query,
  onQuery,
  onPick,
}: {
  index: DatasheetSuggestion[] | null;
  query: string;
  onQuery: (value: string) => void;
  onPick: (suggestion: DatasheetSuggestion) => void;
}) {
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(0);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClick(event: MouseEvent) {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const matches = useMemo(() => {
    const needle = query.trim().toUpperCase();
    if (!needle || index === null) return [];
    const hits = index.filter(
      (s) =>
        s.label.toUpperCase().includes(needle) ||
        (s.document_no ?? "").toUpperCase().includes(needle) ||
        (s.description ?? "").toUpperCase().includes(needle),
    );
    // What the engineer typed the start of comes before what merely
    // contains it, and a part number before a file name: they searched
    // for a part far more often than for a document.
    hits.sort((a, b) => {
      const starts = Number(b.label.toUpperCase().startsWith(needle)) - Number(a.label.toUpperCase().startsWith(needle));
      if (starts) return starts;
      const kind = Number(b.kind === "part") - Number(a.kind === "part");
      if (kind) return kind;
      return a.label.localeCompare(b.label);
    });
    return hits.slice(0, MAX_SUGGESTIONS);
  }, [query, index]);

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (!open || matches.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setHighlighted((i) => (i + 1) % matches.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlighted((i) => (i - 1 + matches.length) % matches.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      onPick(matches[highlighted] ?? matches[0]);
      setOpen(false);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div ref={box} className="relative mt-1">
      <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">
        <SearchIcon />
      </span>
      <input
        type="search"
        value={query}
        disabled={index === null}
        placeholder="Search files, part numbers or description..."
        onChange={(event) => {
          onQuery(event.target.value);
          setHighlighted(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        className="w-full rounded-lg border border-gray-300 py-2 pl-9 pr-3 text-sm focus:border-brand-500 focus:outline-none disabled:bg-gray-50"
      />
      {open && matches.length > 0 && (
        <ul className="absolute z-20 mt-1 max-h-80 w-full overflow-auto rounded-lg border border-gray-200 bg-white py-1 shadow-lg">
          {matches.map((s, i) => (
            <li key={`${s.kind}:${s.label}:${s.path}`}>
              <button
                type="button"
                onMouseEnter={() => setHighlighted(i)}
                onClick={() => {
                  onPick(s);
                  setOpen(false);
                }}
                className={`flex w-full items-start gap-2 px-3 py-2 text-left text-sm ${i === highlighted ? "bg-brand-50" : ""}`}
              >
                <span
                  className={`mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                    s.kind === "part" ? "bg-brand-100 text-brand-700" : "bg-gray-100 text-gray-600"
                  }`}
                >
                  {s.kind}
                </span>
                <span className="min-w-0">
                  <span className="block truncate font-medium text-navy-900">{s.label}</span>
                  {(s.description || s.document_no) && (
                    <span className="block truncate text-xs text-gray-500">
                      {s.document_no ? `${s.document_no} · ` : ""}
                      {s.description}
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

/* --- the card for whichever sheet is selected ------------------------------ */

function FeaturedCard({ file, parts }: { file: DatasheetFile; parts: string[] }) {
  const href = datasheetHref(file.library, file.path);
  const thumbnail = apiUrl(
    `/design-rules/datasheets/thumbnail?${new URLSearchParams({ library: file.library, path: file.path })}`,
  );
  return (
    <section className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap gap-4">
        <img
          src={thumbnail}
          alt=""
          loading="lazy"
          className="h-40 w-32 shrink-0 rounded-lg border border-gray-200 bg-gray-50 object-cover object-top"
        />
        <div className="min-w-[16rem] flex-1">
          <span className="inline-block rounded bg-gray-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-gray-600">
            {topFolder(file.folder) || "Library root"}
          </span>
          <h3 className="mt-1.5 text-xl font-bold text-navy-900">{sheetName(file)}</h3>
          <p className="mt-0.5 text-xs text-gray-500">
            {file.library} <span className="text-gray-300">|</span> {file.filename}
            {!file.reference_no && (
              /* The sheet prints no document number and none has been
               * entered, so its file name is all there is to call it. */
              <>
                {" "}
                <span className="text-gray-300">|</span> no document number
              </>
            )}
          </p>
          {parts.length > 0 ? (
            <>
              <p className="mt-3 text-xs font-semibold text-gray-500">Parts documented by this sheet</p>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {parts.slice(0, 12).map((part) => (
                  <span key={part} className="rounded bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700">
                    {part}
                  </span>
                ))}
                {parts.length > 12 && <span className="px-1 text-xs text-gray-500">+{parts.length - 12} more</span>}
              </div>
            </>
          ) : (
            /* No link records a part against this sheet -- which usually
             * means the file is named for its part and needs none. */
            <p className="mt-3 text-xs text-gray-500">No part numbers are recorded against this sheet.</p>
          )}
          {file.unreadable && <p className="mt-2 text-xs font-medium text-red-600">This file could not be read.</p>}
        </div>

        <div className="flex min-w-[13rem] flex-col gap-3">
          <div className="flex gap-2">
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              className="flex-1 rounded-lg bg-brand-600 px-3 py-2 text-center text-sm font-semibold text-white hover:bg-brand-700"
            >
              View PDF
            </a>
            <a
              href={href}
              download={file.filename}
              className="flex-1 rounded-lg border border-gray-300 bg-white px-3 py-2 text-center text-sm font-semibold text-navy-900 hover:bg-gray-50"
            >
              Download
            </a>
          </div>
          <dl className="space-y-1.5 text-sm">
            <Fact label="File size" value={fileSize(file.size)} />
            <Fact label="Pages" value={`${file.pages} page${file.pages === 1 ? "" : "s"}`} />
            <Fact label="Updated" value={whenModified(file.modified)} />
            <Fact label="Filed under" value={file.folder || "Library root"} />
          </dl>
        </div>
      </div>
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-xs text-gray-500">{label}</dt>
      <dd className="truncate text-right font-medium text-navy-900">{value}</dd>
    </div>
  );
}

/* --- the two ways of listing ----------------------------------------------- */

interface ListProps {
  files: DatasheetFile[];
  partsByPath: Map<string, string[]>;
  selected: DatasheetFile | null;
  onSelect: (file: DatasheetFile) => void;
}

function ListView({ files, partsByPath, selected, onSelect }: ListProps) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-100 text-left text-xs font-semibold text-gray-500">
            <th className="w-10 px-4 py-2">#</th>
            <th className="px-2 py-2">Document</th>
            <th className="px-2 py-2">Parts on this sheet</th>
            <th className="px-2 py-2 text-right">Size</th>
            <th className="px-2 py-2 text-right">Pages</th>
            <th className="px-2 py-2">Updated</th>
            <th className="px-4 py-2 text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          {files.map((file, i) => {
            const parts = partsByPath.get(file.path) ?? [];
            const active = selected?.path === file.path && selected?.library === file.library;
            return (
              <tr
                key={`${file.library}/${file.path}`}
                onClick={() => onSelect(file)}
                className={`cursor-pointer border-b border-gray-50 last:border-0 ${active ? "bg-brand-50/60" : "hover:bg-gray-50"}`}
              >
                <td className="px-4 py-2 text-xs text-gray-400">{String(i + 1).padStart(2, "0")}</td>
                <td className="max-w-[18rem] px-2 py-2">
                  <span className="block truncate font-medium text-brand-700">{sheetName(file)}</span>
                  {file.reference_no && <span className="block truncate text-xs text-gray-500">{file.filename}</span>}
                </td>
                <td className="max-w-[20rem] truncate px-2 py-2 text-gray-600">
                  {parts.length > 0 ? parts.join(", ") : <span className="text-gray-400">—</span>}
                </td>
                <td className="whitespace-nowrap px-2 py-2 text-right text-gray-600">{fileSize(file.size)}</td>
                <td className="px-2 py-2 text-right text-gray-600">{file.pages}</td>
                <td className="whitespace-nowrap px-2 py-2 text-gray-600">{whenModified(file.modified)}</td>
                <td className="px-4 py-2">
                  <RowActions file={file} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function GridView({ files, partsByPath, selected, onSelect }: ListProps) {
  return (
    <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
      {files.map((file) => {
        const parts = partsByPath.get(file.path) ?? [];
        const active = selected?.path === file.path && selected?.library === file.library;
        return (
          <button
            key={`${file.library}/${file.path}`}
            type="button"
            onClick={() => onSelect(file)}
            className={`flex gap-3 rounded-lg border p-3 text-left transition ${
              active ? "border-brand-400 bg-brand-50/60" : "border-gray-200 hover:border-brand-300"
            }`}
          >
            <img
              src={apiUrl(
                `/design-rules/datasheets/thumbnail?${new URLSearchParams({ library: file.library, path: file.path })}`,
              )}
              alt=""
              loading="lazy"
              className="h-20 w-16 shrink-0 rounded border border-gray-200 bg-gray-50 object-cover object-top"
            />
            <span className="min-w-0">
              <span className="block truncate text-sm font-semibold text-navy-900">{sheetName(file)}</span>
              <span className="mt-0.5 block text-xs text-gray-500">
                {fileSize(file.size)} · {file.pages} page{file.pages === 1 ? "" : "s"}
              </span>
              <span className="mt-1 block truncate text-xs text-gray-600">
                {parts.length > 0 ? parts.join(", ") : file.document_no ?? "—"}
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

function RowActions({ file }: { file: DatasheetFile }) {
  const href = datasheetHref(file.library, file.path);
  return (
    <span className="flex justify-end gap-1" onClick={(event) => event.stopPropagation()}>
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        title="View"
        className="rounded border border-gray-200 p-1.5 text-gray-600 hover:bg-gray-50"
      >
        <EyeIcon />
      </a>
      <a
        href={href}
        download={file.filename}
        title="Download"
        className="rounded border border-gray-200 p-1.5 text-gray-600 hover:bg-gray-50"
      >
        <DownloadIcon />
      </a>
    </span>
  );
}

/* --- small helpers --------------------------------------------------------- */

/** The section a sheet is filed under, without the library's ordering
 * prefix: "01- PANEL/Concealed" is a PANEL sheet. */
function topFolder(folder: string): string {
  const first = folder.split("/")[0] ?? "";
  return first.replace(/^\d+\s*-\s*/, "").toUpperCase();
}

/** What to call a sheet: its manufacturer's document number, and its file
 * name only when it prints none. The number is what an engineer quotes,
 * and what the library's file names never carried. */
function sheetName(file: DatasheetFile): string {
  return file.reference_no ?? stem(file.filename);
}

function stem(filename: string): string {
  return filename.replace(/\.pdf$/i, "").replace(/^\d+\s*-\s*/, "");
}

function fileSize(bytes: number): string {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The API sends seconds since the epoch; 0 means the index never recorded
 * one, which is not a date in 1970. */
function whenModified(seconds: number): string {
  if (!seconds) return "—";
  return new Date(seconds * 1000).toLocaleDateString(undefined, { dateStyle: "medium" });
}

function ViewToggle({
  active,
  onClick,
  label,
  children,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      aria-pressed={active}
      className={`px-2.5 py-1.5 ${active ? "bg-brand-600 text-white" : "bg-white text-gray-500 hover:bg-gray-50"}`}
    >
      {children}
    </button>
  );
}

/* --- icons ----------------------------------------------------------------- */

const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

const SheetIcon = ({ small }: { small?: boolean }) => (
  <svg viewBox="0 0 24 24" className={small ? "h-4 w-4" : "h-6 w-6"} {...stroke}>
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
    <path d="M14 3v5h5M9 13h6M9 17h4" />
  </svg>
);

/** A mark for each system, so the four cards are told apart at a glance:
 * a flame, a running figure, a battery, a cable. */
function SystemIcon({ code }: { code: string }) {
  if (code === "ELS_SELF_CONTAINED") {
    return (
      <svg viewBox="0 0 24 24" className="h-6 w-6" {...stroke}>
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M13 8.5 10 13h3l-1 3.5 3-4.5h-3z" />
      </svg>
    );
  }
  if (code === "ELS_CENTRAL_BATTERY") {
    return (
      <svg viewBox="0 0 24 24" className="h-6 w-6" {...stroke}>
        <rect x="6" y="3" width="12" height="18" rx="2" />
        <path d="M10 3V2h4v1M12 8v7M9 11.5h6" />
      </svg>
    );
  }
  if (code === "FRC") {
    return (
      <svg viewBox="0 0 24 24" className="h-6 w-6" {...stroke}>
        <path d="M4 17c4 0 4-10 8-10s4 10 8 10" />
        <path d="M3 20h4M17 4h4" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" className="h-6 w-6" {...stroke}>
      <path d="M12 2.5S7 7 7 13a5 5 0 0 0 10 0c0-2-1-3.5-2-5-1 1.5-1.5 2-2.5 2 .5-3-.5-5.5 0-7.5z" />
    </svg>
  );
}

const SearchIcon = () => (
  <svg viewBox="0 0 24 24" className="h-4 w-4" {...stroke}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </svg>
);
const EyeIcon = () => (
  <svg viewBox="0 0 24 24" className="h-4 w-4" {...stroke}>
    <path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6-10-6-10-6" />
    <circle cx="12" cy="12" r="2.5" />
  </svg>
);
const DownloadIcon = () => (
  <svg viewBox="0 0 24 24" className="h-4 w-4" {...stroke}>
    <path d="M12 3v12m0 0 4-4m-4 4-4-4M4 19h16" />
  </svg>
);
const ListIcon = () => (
  <svg viewBox="0 0 24 24" className="h-4 w-4" {...stroke}>
    <path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01" />
  </svg>
);
const GridIcon = () => (
  <svg viewBox="0 0 24 24" className="h-4 w-4" {...stroke}>
    <rect x="3" y="3" width="7" height="7" rx="1" />
    <rect x="14" y="3" width="7" height="7" rx="1" />
    <rect x="3" y="14" width="7" height="7" rx="1" />
    <rect x="14" y="14" width="7" height="7" rx="1" />
  </svg>
);
