import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import { useLocation } from "react-router-dom";

import { useEscapeKey } from "@/hooks/useEscapeKey";
import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";
import { areaForRoute, buildDiagnostics } from "@/lib/diagnostics";
import { ISSUE_REPO, openIssueDraft, type IssueKind } from "@/lib/github-issue";
import { AREAS, type Area } from "@/lib/page-context";

/**
 * "Report an issue" — writes a GitHub issue draft, does not file it.
 *
 * The button hands off to GitHub's own form with every field prefilled; the
 * user submits it themselves. That keeps the report attributed to them, keeps
 * the reply thread in their notifications, and keeps this install free of a
 * token that could write to the tracker.
 *
 * The diagnostics block is opt-in and shown verbatim before it leaves: it ends
 * up in a public issue, so the person attaching it gets to read it first.
 */
export function ReportIssueDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [kind, setKind] = useState<IssueKind>("bug");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [detail, setDetail] = useState("");
  const [area, setArea] = useState<Area | null>(null);
  const [includeDiagnostics, setIncludeDiagnostics] = useState(true);
  const [showDiagnostics, setShowDiagnostics] = useState(false);

  const { pathname, search } = useLocation();
  const { server } = useServer();
  // Version never changes while the tab is open, and a failed fetch must not
  // block the report — the block just says the version is unavailable.
  const { data: env } = useQuery({
    queryKey: ["meta-env"],
    queryFn: api.getEnv,
    staleTime: Infinity,
    retry: false,
    enabled: open,
  });

  useEscapeKey(open, onClose);

  if (!open) return null;

  const isBug = kind === "bug";
  const route = pathname + search;
  // The page the user is on is the best guess at the area, and the best guess
  // is what a dropdown should open on — `area` only holds an explicit override.
  const effectiveArea = area ?? areaForRoute(route);
  const diagnostics = buildDiagnostics({
    kind,
    route,
    server,
    env: env ?? null,
  });
  const canSubmit = title.trim().length > 0 && description.trim().length > 0;

  const submit = () => {
    if (!canSubmit) return;
    openIssueDraft({
      kind,
      title,
      area: effectiveArea,
      description,
      detail,
      diagnostics: includeDiagnostics ? diagnostics : undefined,
    });
    onClose();
    setTitle("");
    setDescription("");
    setDetail("");
    setArea(null);
  };

  const inputClass =
    "w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="max-h-full w-full max-w-lg overflow-y-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold text-[var(--color-text)]">Report an issue</h2>
        <p className="mt-1 mb-4 text-xs text-[var(--color-text-muted)]">
          Opens a prefilled issue on{" "}
          <span className="font-mono">{ISSUE_REPO}</span> in a new tab. You review and
          submit it under your own GitHub account — nothing is sent from here. Text
          that looks like a key, token or password is masked on the way out, so the
          form can differ slightly from what you typed.
        </p>

        <div className="mb-4 flex gap-1 rounded-lg bg-[var(--color-bg)] p-1">
          {(["bug", "feature"] as const).map((k) => (
            <button
              key={k}
              onClick={() => setKind(k)}
              className={`flex-1 rounded-md px-3 py-1.5 text-sm transition-colors ${
                kind === k
                  ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              {k === "bug" ? "Something is broken" : "Feature idea"}
            </button>
          ))}
        </div>

        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-muted)]">
              Title <span className="text-[var(--color-red)]">*</span>
            </label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className={inputClass}
              placeholder={
                isBug ? "Portfolio shows a blank chart" : "Filter executors by connector"
              }
              autoFocus
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-muted)]">
              Area
            </label>
            <select
              value={effectiveArea}
              onChange={(e) => setArea(e.target.value as Area)}
              className={inputClass}
            >
              {AREAS.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
            <p className="mt-1 text-[10px] text-[var(--color-text-muted)]/70">
              Preselected from the page you were on.
            </p>
          </div>

          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-muted)]">
              {isBug ? "What happened" : "What should it do"}{" "}
              <span className="text-[var(--color-red)]">*</span>
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={4}
              className={`${inputClass} resize-y`}
              placeholder={
                isBug
                  ? "What you expected, and what you got instead."
                  : "Describe the behavior you want."
              }
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-muted)]">
              {isBug ? "Steps to reproduce" : "Why it matters"}
            </label>
            <textarea
              value={detail}
              onChange={(e) => setDetail(e.target.value)}
              rows={3}
              className={`${inputClass} resize-y`}
              placeholder={
                isBug ? "1.\n2.\n3." : "What this would let you do that you cannot today."
              }
            />
          </div>

          <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-3">
            <label className="flex cursor-pointer items-start gap-2">
              <input
                type="checkbox"
                checked={includeDiagnostics}
                onChange={(e) => setIncludeDiagnostics(e.target.checked)}
                className="mt-0.5 accent-[var(--color-primary)]"
              />
              <span className="text-xs text-[var(--color-text)]">
                Attach diagnostics
                <span className="block text-[10px] text-[var(--color-text-muted)]">
                  {isBug
                    ? "Build version, page, live-data status, failing requests and recent console errors."
                    : "Build version and the page you were on — nothing else."}{" "}
                  Becomes part of a public issue — read it first.
                </span>
              </span>
            </label>

            {includeDiagnostics && (
              <>
                <button
                  onClick={() => setShowDiagnostics((v) => !v)}
                  className="mt-2 flex items-center gap-1 text-[11px] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                >
                  {showDiagnostics ? (
                    <ChevronDown className="h-3 w-3" />
                  ) : (
                    <ChevronRight className="h-3 w-3" />
                  )}
                  {showDiagnostics ? "Hide" : "Show"} what will be attached
                </button>
                {showDiagnostics && (
                  <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-[var(--color-surface)] p-2 font-mono text-[10px] text-[var(--color-text-muted)]">
                    {diagnostics}
                  </pre>
                )}
              </>
            )}
          </div>
        </div>

        <div className="mt-5 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={!canSubmit}
            className="flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            Continue on GitHub
            <ExternalLink className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}
