import { useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { ApiError, api } from "../lib/api";
import { PROJECT_EDITOR_ROLES, type Project } from "../lib/types";
import { useProject } from "./ProjectWorkspace";

/** The Design Sheet codes the platform reads. Which of them this project
 * needs comes from the backend (`project.system_codes`), which applies the
 * company rules: an Edwards fire alarm's voice evacuation and fire telephone
 * are on the FAS sheet, and emergency lighting (ELS, CBS, EML) is one system. */
const SHEET_CODES = ["FAS", "VES", "PAVA", "ELS"];
const SHEET_ALIASES: Record<string, string> = { CBS: "ELS", EML: "ELS", ELM: "ELS", EL: "ELS", VE: "VES", FT: "FAS", FA: "FAS" };

function fileName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

export function ProjectDocumentsPage() {
  const { project, setProject } = useProject();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function upload(what: string, path: string, file: File, systemCode?: string) {
    setBusy(what);
    setError(null);
    const body = new FormData();
    body.append("file", file);
    if (systemCode) body.append("system_code", systemCode);
    try {
      setProject(await api.upload<Project>(path, body));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Could not upload the ${what}`);
    } finally {
      setBusy(null);
    }
  }

  async function removeSheet(id: number) {
    setBusy(`sheet-${id}`);
    setError(null);
    try {
      setProject(await api.delete<Project>(`/projects/${project.id}/documents/design-sheets/${id}`));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not remove the Design Sheet");
    } finally {
      setBusy(null);
    }
  }

  // A system the project has, with no Design Sheet found for it.
  const effective = (code: string | null) => {
    const upper = (code ?? "").toUpperCase();
    const canonical = SHEET_ALIASES[upper] ?? upper;
    return canonical === "VES" && project.voice_evacuation_integrated ? "FAS" : canonical;
  };
  const found = new Set(project.design_sheets.map((sheet) => effective(sheet.system_code)));
  const missing = project.system_codes.filter((code) => SHEET_CODES.includes(code) && !found.has(code)).map((code) => ({ code }));

  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-bold text-navy-900">Documents</h1>
      <p className="mt-1 text-sm text-gray-500">
        Files matched in the project archive when this project was created. Anything the search did not find can be
        uploaded here instead.
      </p>

      {error && <div className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}

      <section className="mt-6 rounded-xl border border-gray-200 bg-white p-5">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-400">Design Request Form</h2>
        {project.drf_document_path ? (
          <>
            <p className="mt-2 break-all text-sm text-navy-900">{project.drf_document_path}</p>
            {canEdit && (
              <UploadButton
                label="Replace"
                busy={busy === "DRF"}
                onPick={(file) => upload("DRF", `/projects/${project.id}/documents/drf`, file)}
              />
            )}
          </>
        ) : (
          <Missing
            what="The DRF was not found in the project folder."
            canEdit={canEdit}
            busy={busy === "DRF"}
            onPick={(file) => upload("DRF", `/projects/${project.id}/documents/drf`, file)}
          />
        )}
      </section>

      <section className="mt-4 rounded-xl border border-gray-200 bg-white p-5">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-400">Design Sheets</h2>
        {project.design_sheets.length === 0 ? (
          <p className="mt-2 text-sm text-gray-400">No Design Sheets matched.</p>
        ) : (
          <ul className="mt-2 divide-y divide-gray-100">
            {project.design_sheets.map((sheet) => (
              <li key={sheet.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <div className="min-w-0">
                  <div className="text-sm font-medium text-navy-900">{sheet.system_code ?? "Unlabelled system"}</div>
                  <div className="break-all text-xs text-gray-500" title={sheet.document_path}>
                    {fileName(sheet.document_path)}
                  </div>
                </div>
                {canEdit && (
                  <button
                    onClick={() => removeSheet(sheet.id)}
                    disabled={busy === `sheet-${sheet.id}`}
                    className="rounded-lg border border-gray-300 px-2 py-1 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50"
                  >
                    Remove
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}

        {missing.map(({ code }) => (
          <div key={code} className="mt-3 rounded-lg bg-amber-50 px-3 py-2">
            <Missing
              what={`No Design Sheet was found for ${code}, which this project has${
                code === "FAS" && project.voice_evacuation_integrated ? " (it covers Voice Evacuation and Fire Telephone too)" : ""
              }.`}
              canEdit={canEdit}
              busy={busy === `sheet-${code}`}
              onPick={(file) => upload(`sheet-${code}`, `/projects/${project.id}/documents/design-sheets`, file, code)}
            />
          </div>
        ))}

        {canEdit && (
          <AddSheet
            busy={busy === "sheet-new"}
            onPick={(file, code) => upload("sheet-new", `/projects/${project.id}/documents/design-sheets`, file, code)}
          />
        )}
        <p className="mt-3 text-xs text-gray-400">
          A sheet added now is not read into the BOQ: the Design Sheets are read once, when the BOQ is first opened.
          Add its lines under BOQ.
        </p>
      </section>

      <section className="mt-4 rounded-xl border border-gray-200 bg-white p-5">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-400">Source Folder</h2>
        {project.source_folder_path ? (
          <p className="mt-2 break-all text-sm text-navy-900">{project.source_folder_path}</p>
        ) : (
          <p className="mt-2 text-sm text-amber-700">
            No project folder was matched in the archive, so nothing can be read from it automatically.
          </p>
        )}
      </section>

      <p className="mt-4 text-xs text-gray-400">
        Uploaded files are kept by the platform; the project archive is only ever read, never written to.
      </p>
    </div>
  );
}

function Missing({
  what,
  canEdit,
  busy,
  onPick,
}: {
  what: string;
  canEdit: boolean;
  busy: boolean;
  onPick: (file: File) => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <p className="text-sm text-amber-800">{what}</p>
      {canEdit && <UploadButton label="Upload" busy={busy} onPick={onPick} primary />}
    </div>
  );
}

function UploadButton({
  label,
  busy,
  onPick,
  primary,
}: {
  label: string;
  busy: boolean;
  onPick: (file: File) => void;
  primary?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={input}
        type="file"
        accept=".pdf,.xlsx,.xlsm"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onPick(file);
          e.target.value = "";
        }}
      />
      <button
        onClick={() => input.current?.click()}
        disabled={busy}
        className={`mt-2 rounded-lg px-3 py-1.5 text-sm font-semibold disabled:opacity-60 ${
          primary
            ? "bg-brand-600 text-white hover:bg-brand-700"
            : "border border-gray-300 text-gray-700 hover:bg-gray-50"
        }`}
      >
        {busy ? "Uploading..." : label}
      </button>
    </>
  );
}

function AddSheet({ busy, onPick }: { busy: boolean; onPick: (file: File, code: string) => void }) {
  const [code, setCode] = useState("");
  return (
    <div className="mt-3 flex flex-wrap items-end gap-2 border-t border-gray-100 pt-3">
      <label className="text-xs font-medium text-gray-500">
        System
        <input
          value={code}
          onChange={(e) => setCode(e.target.value.toUpperCase())}
          placeholder="FAS"
          className="input mt-1 w-24 py-1.5"
        />
      </label>
      <UploadButton label="Add a Design Sheet" busy={busy} onPick={(file) => onPick(file, code.trim())} />
    </div>
  );
}
