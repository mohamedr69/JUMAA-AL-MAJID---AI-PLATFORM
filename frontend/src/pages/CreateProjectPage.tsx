import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api } from "../lib/api";
import type { Project, ProjectResolveResponse } from "../lib/types";
import { ReviewProjectForm } from "./ReviewProjectForm";

type Step = "input" | "resolving" | "picker" | "review";

export function CreateProjectPage() {
  const navigate = useNavigate();

  const [step, setStep] = useState<Step>("input");
  const [epNumber, setEpNumber] = useState("");
  const [resolution, setResolution] = useState<ProjectResolveResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function resolve(selectedFolder?: string) {
    setError(null);
    setStep("resolving");
    try {
      const result = await api.post<ProjectResolveResponse>("/projects/resolve", {
        ep_number: epNumber,
        selected_folder: selectedFolder,
      });
      setResolution(result);

      if (!result.folder_found) {
        setError(`No project folder found for EP number "${epNumber}".`);
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

  if (step === "review" && resolution) {
    const sourceFolder =
      resolution.matched_folders.length === 1
        ? resolution.matched_folders[0]
        : resolution.matched_folders.find((f) => f) ?? "";
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
          EP number "{epNumber}" matches more than one folder. Choose the correct one.
        </p>
        <div className="mt-4 space-y-2">
          {resolution.matched_folders.map((folder) => (
            <button
              key={folder}
              onClick={() => resolve(folder)}
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
        Enter the EP Number and we&apos;ll find your project files automatically.
      </p>

      <form onSubmit={handleFindProject} className="mt-6 space-y-4">
        <label className="block text-sm font-medium text-gray-700">
          EP Number
          <input
            required
            value={epNumber}
            onChange={(e) => setEpNumber(e.target.value)}
            placeholder="e.g. 29495"
            disabled={step === "resolving"}
            className="input mt-1"
          />
        </label>

        {error && <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}

        <button
          type="submit"
          disabled={step === "resolving"}
          className="w-full rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
        >
          {step === "resolving" ? "Searching..." : "Find Project"}
        </button>
      </form>
    </div>
  );
}
