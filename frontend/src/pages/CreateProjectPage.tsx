import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { EpNumberSearch } from "../components/EpNumberSearch";
import { ApiError, api } from "../lib/api";
import type { ArchiveStatus, ArchiveSuggestion, Project, ProjectResolveResponse } from "../lib/types";
import { ReviewProjectForm } from "./ReviewProjectForm";

type Step = "input" | "resolving" | "picker" | "review";

export function CreateProjectPage() {
  const navigate = useNavigate();

  const [step, setStep] = useState<Step>("input");
  const [epNumber, setEpNumber] = useState("");
  const [resolution, setResolution] = useState<ProjectResolveResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The archive index behind the search box: how many projects it can
  // suggest, and why it cannot suggest any when it cannot.
  const [archive, setArchive] = useState<ArchiveStatus | null>(null);
  // The project this EP number already has, when the search box said so.
  const [existing, setExisting] = useState<ArchiveSuggestion | null>(null);
  const [rescanning, setRescanning] = useState(false);

  const scanning = archive?.scanning ?? false;

  useEffect(() => {
    let cancelled = false;
    // The status is a hint beside the box, never a blocker: a failure to
    // read it leaves the box working exactly as it does without an index.
    function read() {
      api
        .get<ArchiveStatus>("/archive/status")
        .then((status) => !cancelled && setArchive(status))
        .catch(() => undefined);
    }
    read();
    // The first scan of the archive takes a little while and finishes
    // without anyone asking; while it runs, the note follows it, so the
    // count appears rather than the page having to be reloaded.
    const timer = scanning ? window.setInterval(read, 4000) : undefined;
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [scanning]);

  async function resolve(selectedFolder?: string, number: string = epNumber) {
    setError(null);
    setStep("resolving");
    try {
      const result = await api.post<ProjectResolveResponse>("/projects/resolve", {
        ep_number: number,
        selected_folder: selectedFolder,
      });
      setResolution(result);

      if (!result.folder_found) {
        setError(`No project folder found for EP number "${number}".`);
        setStep("input");
        return;
      }
      if (result.is_ambiguous && !selectedFolder) {
        setStep("picker");
        return;
      }
      setStep("review");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to search for the project");
      setStep("input");
    }
  }

  function handleFindProject(e: FormEvent) {
    e.preventDefault();
    if (epNumber.trim()) resolve();
  }

  /** A suggestion was chosen. One filed in a single folder goes straight
   * on -- the folder is known, so there is nothing left to ask. One filed
   * in several still goes through the picker, and one the platform
   * already has offers to open it rather than create it twice. */
  function handlePick(suggestion: ArchiveSuggestion) {
    setExisting(suggestion.project_id !== null ? suggestion : null);
    if (suggestion.project_id !== null) return;
    resolve(undefined, suggestion.ep_number);
  }

  async function rescan() {
    setRescanning(true);
    setError(null);
    try {
      await api.post("/archive/scan");
      setArchive(await api.get<ArchiveStatus>("/archive/status"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start a scan of the archive");
    } finally {
      setRescanning(false);
    }
  }

  if (step === "review" && resolution) {
    // The folder the resolver read the documents from: where the EP number
    // matched several, the one the engineer picked. Taking the first match
    // here filed EP-31112 under one contractor with its DRF under another.
    const sourceFolder = resolution.source_folder ?? resolution.matched_folders[0] ?? "";
    return (
      <ReviewProjectForm
        // As the server normalised it: "EP-29495 " typed is "29495".
        epNumber={resolution.ep_number}
        sourceFolderPath={sourceFolder}
        resolution={resolution}
        onCreated={(project: Project) =>
          navigate(`/projects/${project.id}`, { replace: true, state: { justCreated: true } })
        }
      />
    );
  }

  if (step === "picker" && resolution) {
    return (
      <div className="mx-auto max-w-xl">
        <h1 className="text-xl font-bold text-navy-900">Multiple folders found</h1>
        <p className="mt-1 text-sm text-gray-500">
          EP number "{resolution.ep_number}" matches more than one folder. Choose the correct one.
        </p>
        <div className="mt-4 space-y-2">
          {resolution.matched_folders.map((folder) => (
            <button
              key={folder}
              onClick={() => resolve(folder, resolution.ep_number)}
              className="w-full rounded-lg border border-gray-200 bg-white px-4 py-3 text-left text-sm hover:border-brand-400 hover:bg-brand-50"
            >
              {folder}
            </button>
          ))}
        </div>
        <button
          onClick={() => setStep("input")}
          className="mt-4 text-sm text-gray-500 hover:text-gray-700"
        >
          &larr; Back
        </button>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-md">
      <h1 className="text-xl font-bold text-navy-900">Create New Project</h1>
      <p className="mt-1 text-sm text-gray-500">
        Start typing an EP number or a project name — the archive suggests the rest, and we&apos;ll
        find the project files automatically.
      </p>

      <form onSubmit={handleFindProject} className="mt-6 space-y-4">
        <label htmlFor="ep-number" className="block text-sm font-medium text-gray-700">
          EP Number
        </label>
        <EpNumberSearch
          id="ep-number"
          value={epNumber}
          onChange={(value) => {
            setEpNumber(value);
            setExisting(null);
          }}
          onPick={handlePick}
          onSubmit={() => epNumber.trim() && resolve()}
          disabled={step === "resolving"}
        />

        {existing && (
          <div className="rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">
            EP-{existing.ep_number}
            {existing.project_name && ` — ${existing.project_name}`} is already in the platform.
            <button
              type="button"
              onClick={() => navigate(`/projects/${existing.project_id}`)}
              className="ml-2 font-semibold underline hover:no-underline"
            >
              Open it
            </button>
          </div>
        )}

        {error && <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}

        <button
          type="submit"
          disabled={step === "resolving"}
          className="w-full rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
        >
          {step === "resolving" ? "Searching..." : "Find Project"}
        </button>
      </form>

      <ArchiveNote status={archive} onRescan={rescan} rescanning={rescanning} />
    </div>
  );
}

/** What the search box can and cannot do right now, in one line. An index
 * still being built is the one case worth explaining: the box suggests
 * nothing, but a number typed out in full still finds its folder. */
function ArchiveNote({
  status,
  onRescan,
  rescanning,
}: {
  status: ArchiveStatus | null;
  onRescan: () => void;
  rescanning: boolean;
}) {
  if (status === null) return null;

  if (!status.configured) {
    return (
      <p className="mt-4 text-xs text-gray-400">
        This PC has no project archive configured (PROJECTS_ROOT), so there is nothing to suggest
        and no folder to find.
      </p>
    );
  }
  if (!status.reachable) {
    return (
      <p className="mt-4 text-xs text-amber-600">
        The project archive is not reachable from this PC right now. Suggestions come from the last
        scan; opening a folder will need OneDrive back.
      </p>
    );
  }
  if (!status.searchable) {
    return (
      <p className="mt-4 text-xs text-gray-400">
        {status.scanning || status.scan_status === "scanning"
          ? "Reading the archive for the first time — suggestions will appear shortly. "
          : "The archive has not been indexed on this PC yet, so there is nothing to suggest. "}
        An EP number typed in full is still found.{" "}
        {!status.scanning && (
          <button type="button" onClick={onRescan} disabled={rescanning} className="underline hover:no-underline">
            {rescanning ? "Starting…" : "Index it now"}
          </button>
        )}
      </p>
    );
  }
  return (
    <p className="mt-4 text-xs text-gray-400">
      {status.projects.toLocaleString()} projects in the archive
      {status.last_successful_scan_at &&
        `, last read ${new Date(status.last_successful_scan_at).toLocaleString()}`}
      .{" "}
      <button type="button" onClick={onRescan} disabled={rescanning || status.scanning} className="underline hover:no-underline">
        {status.scanning ? "Reading…" : rescanning ? "Starting…" : "Check for new projects"}
      </button>
      {status.scan_status === "partial" && (
        <span className="text-amber-600"> Some folders could not be read on the last pass.</span>
      )}
    </p>
  );
}
