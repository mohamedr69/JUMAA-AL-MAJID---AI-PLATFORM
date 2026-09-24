import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { ApiError, api, apiUrl } from "../lib/api";
import { PROJECT_EDITOR_ROLES, type ReplyRow, type SubmittalReply } from "../lib/types";

/** The reply to the consultant's comments on one submittal revision.
 *
 * A returned submittal comes back with remarks; this answers them one by
 * one and goes out with the next revision. It opens in a window of its
 * own because it is written beside the consultant's own sheet, not
 * inside the register.
 *
 * The comments are **not** read out of the consultant's scan. What the
 * sheet opens with is the text of the reply the platform found, split
 * into lines to answer; the rest is the engineer's to add. Inventing
 * rows would mean checking every one before trusting any.
 *
 * Saving is not submitting: the submittal's status, and the action
 * asking for the next revision, are left alone until that revision is
 * actually filed.
 */
export function SubmittalReplyPage() {
  const { id, reference = "", revision = "" } = useParams();
  const { user } = useAuth();
  const canEdit = user !== null && PROJECT_EDITOR_ROLES.includes(user.role);

  const [sheet, setSheet] = useState<SubmittalReply | null>(null);
  const [rows, setRows] = useState<ReplyRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  const base = `/projects/${id}/submittals/${encodeURIComponent(reference)}/${encodeURIComponent(revision)}/reply`;

  useEffect(() => {
    let live = true;
    api
      .get<SubmittalReply>(base)
      .then((got) => {
        if (!live) return;
        setSheet(got);
        setRows(got.rows);
      })
      .catch((err) => live && setError(err instanceof ApiError ? err.message : "The reply could not be opened"));
    return () => {
      live = false;
    };
  }, [base]);

  function edit(index: number, field: keyof ReplyRow, value: string) {
    setRows((current) =>
      current.map((row, i) => (i === index ? { ...row, [field]: field === "sn" ? Number(value) || 0 : value } : row)),
    );
    setDirty(true);
  }

  function addRow() {
    setRows((current) => [...current, { sn: current.length + 1, comment: "", reply: "Comply", remark: "" }]);
    setDirty(true);
  }

  function removeRow(index: number) {
    // Renumbered, because the SN column is what the consultant's own
    // sheet is read against.
    setRows((current) => current.filter((_, i) => i !== index).map((row, i) => ({ ...row, sn: i + 1 })));
    setDirty(true);
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const got = await api.put<SubmittalReply>(base, {
        rows,
        consultant: sheet?.consultant ?? null,
        manufacturer: sheet?.manufacturer ?? null,
      });
      setSheet(got);
      setRows(got.rows);
      setDirty(false);
      setSaved(new Date().toLocaleTimeString());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The reply could not be saved");
    } finally {
      setSaving(false);
    }
  }

  if (error && sheet === null) {
    return <div className="mx-auto max-w-3xl rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>;
  }
  if (sheet === null) return <div className="mx-auto max-w-3xl text-sm text-gray-400">Opening the reply...</div>;

  return (
    <div className="mx-auto max-w-6xl">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-navy-900">
            Reply to Consultant Comments on {sheet.system_title} submittal
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            {sheet.saved
              ? "Saved. Edit it and save again; it is not sent until you export it."
              : "Not saved yet. The comments below are what the consultant's reply says — check them, then answer each."}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {canEdit && (
            <button
              type="button"
              onClick={save}
              disabled={saving || !dirty}
              className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
            >
              {saving ? "Saving..." : dirty ? "Save" : "Saved"}
            </button>
          )}
          <a
            href={apiUrl(`${base}.pdf`)}
            className="rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-semibold text-navy-900 hover:bg-gray-50"
          >
            Export
          </a>
        </div>
      </header>

      {error && <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      {saved && !dirty && <p className="mt-2 text-xs text-emerald-700">Saved at {saved}</p>}

      {/* The heading block the exported sheet carries. */}
      <section className="mt-4 overflow-hidden rounded-xl border border-gray-300">
        <Line label="Project" value={sheet.project_name} />
        <Line label="Ref No" value={`${sheet.reference} - ${sheet.revision}`} />
        <Line label="MANUFACTURER" value={sheet.manufacturer} />
        <Line label="Consultant" value={sheet.consultant} />
      </section>

      <section className="mt-4 overflow-x-auto rounded-xl border border-gray-300">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="bg-emerald-100 text-left text-xs font-bold text-navy-900">
              <th className="w-12 border border-gray-300 px-2 py-2 text-center">SN</th>
              <th className="border border-gray-300 px-2 py-2">Consultant Comments</th>
              <th className="w-40 border border-gray-300 px-2 py-2 text-center">{sheet.supplier} Reply</th>
              <th className="w-72 border border-gray-300 px-2 py-2">REMARKS</th>
              {canEdit && <th className="w-10 border border-gray-300 px-2 py-2" />}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index} className="align-top">
                <td className="border border-gray-300 px-2 py-1 text-center text-gray-500">{row.sn}</td>
                <td className="border border-gray-300 p-0">
                  <Cell value={row.comment} readOnly={!canEdit} onChange={(v) => edit(index, "comment", v)} rows={3} />
                </td>
                <td className="border border-gray-300 p-0">
                  <Cell
                    value={row.reply}
                    readOnly={!canEdit}
                    onChange={(v) => edit(index, "reply", v)}
                    rows={3}
                    className="text-center font-semibold text-blue-700"
                  />
                </td>
                <td className="border border-gray-300 p-0">
                  <Cell value={row.remark} readOnly={!canEdit} onChange={(v) => edit(index, "remark", v)} rows={3} />
                </td>
                {canEdit && (
                  <td className="border border-gray-300 px-1 text-center">
                    <button
                      type="button"
                      onClick={() => removeRow(index)}
                      title="Remove this comment"
                      className="text-gray-400 hover:text-red-600"
                    >
                      ×
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {canEdit && (
        <button type="button" onClick={addRow} className="mt-3 text-sm font-semibold text-brand-600 hover:underline">
          + Add a comment
        </button>
      )}
      <p className="mt-4 text-xs text-gray-400">
        Saving keeps the sheet to come back to. It does not change where the submittal stands: that follows the next
        revision being filed.
      </p>
    </div>
  );
}

function Line({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="flex border-b border-gray-300 last:border-0 bg-white text-sm">
      <span className="w-40 shrink-0 px-3 py-2 font-bold text-navy-900">{label}</span>
      <span className="px-3 py-2 text-gray-700">{value || "—"}</span>
    </div>
  );
}

/** A cell that grows with what is typed in it, so a long comment is read
 * in full rather than through a one-line window. */
function Cell({
  value,
  onChange,
  readOnly,
  rows,
  className = "",
}: {
  value: string;
  onChange: (value: string) => void;
  readOnly: boolean;
  rows: number;
  className?: string;
}) {
  return (
    <textarea
      value={value}
      readOnly={readOnly}
      rows={rows}
      onChange={(event) => onChange(event.target.value)}
      className={`w-full resize-y border-0 bg-transparent px-2 py-1.5 text-sm focus:bg-brand-50/40 focus:outline-none ${className}`}
    />
  );
}
