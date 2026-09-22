import { apiUrl } from "../lib/api";
import type { DatasheetFile } from "../lib/types";

/** The address the browser opens a library file at. Inline, so the PDF
 * viewer opens it rather than downloading; `#page=N` is honoured. */
export function datasheetHref(library: string, path: string, page?: number): string {
  const query = new URLSearchParams({ library, path });
  return apiUrl(`/design-rules/datasheets/file?${query}${page ? `#page=${page}` : ""}`);
}

/** The library's own filing, kept in the order the endpoint returns. */
export function groupByFolder(files: DatasheetFile[]): [string, DatasheetFile[]][] {
  const sections = new Map<string, DatasheetFile[]>();
  for (const file of files) {
    const rows = sections.get(file.folder);
    if (rows) rows.push(file);
    else sections.set(file.folder, [file]);
  }
  return [...sections.entries()];
}

/** One datasheet as a row: its name opens it, and what the platform could
 * read of it is said beside it. A file it could not read is marked --
 * silence there would read as a sheet with nothing in it. */
export function DatasheetRow({ file }: { file: DatasheetFile }) {
  return (
    <li className="flex flex-wrap items-center gap-2 py-1.5 text-sm">
      <a
        href={datasheetHref(file.library, file.path)}
        target="_blank"
        rel="noreferrer"
        className="min-w-0 flex-1 truncate font-medium text-brand-600 hover:underline"
      >
        {file.filename}
      </a>
      <span className="text-xs text-gray-500">
        {file.document_no ?? "no document number"} · {file.pages} page{file.pages === 1 ? "" : "s"}
      </span>
      {file.unreadable && <span className="text-xs font-medium text-red-600">unreadable</span>}
    </li>
  );
}

/** Every datasheet given, under the folder the library files it in. Shared
 * by the project's Documents tab and the global Datasheets page so the two
 * cannot drift apart. */
export function DatasheetFileList({ files }: { files: DatasheetFile[] }) {
  return (
    <>
      {groupByFolder(files).map(([folder, rows]) => (
        <div key={folder} className="mt-3">
          <h3 className="text-xs font-semibold text-navy-900">{folder || "Library root"}</h3>
          <ul className="mt-1 divide-y divide-gray-100">
            {rows.map((f) => (
              <DatasheetRow key={`${f.library}/${f.path}`} file={f} />
            ))}
          </ul>
        </div>
      ))}
    </>
  );
}
