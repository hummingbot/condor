import { AlertTriangle, Check, FileCode2, FileText, Loader2, Upload } from "lucide-react";
import { useRef, useState } from "react";

import { api, type AgentImportPlan } from "@/lib/api";

/**
 * Pick a bundle, read what it would do, then let it land.
 *
 * Two steps and not one, because the review *is* the sandbox. An imported
 * routine runs inside the Condor process with no isolation and this feature
 * builds none; what it provides is that every executable file is named here
 * before anything is written, that the code arrives muted, and that nothing is
 * started. The copy says exactly that rather than implying a guarantee that
 * does not exist.
 *
 * The preview and the apply call the same builder on the server, so what this
 * dialog renders is what the write does.
 */
export function ImportAgentDialog({ onImported }: { onImported: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [plan, setPlan] = useState<AgentImportPlan | null>(null);
  const [asSlug, setAsSlug] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [landed, setLanded] = useState<AgentImportPlan | null>(null);
  /**
   * Which preview the screen belongs to.
   *
   * Two bundles chosen in a row are two requests, and the first one can answer
   * second: the plan on screen would then be A's files and A's refusals while
   * `file` is B, and Import — which submits the reviewed plan's name — would
   * write B under a name nobody reviewed it for. Only the newest request may
   * render, so what is shown is always what the pending import would do.
   */
  const latest = useRef(0);

  async function pick(chosen: File | null) {
    setFile(chosen);
    setPlan(null);
    setLanded(null);
    setError(null);
    setAsSlug("");
    if (!chosen) return;
    await review(chosen, "");
  }

  async function review(chosen: File, slug: string) {
    const mine = ++latest.current;
    setBusy(true);
    setError(null);
    try {
      const previewed = await api.previewAgentImport(chosen, slug);
      if (mine !== latest.current) return;
      setPlan(previewed);
    } catch (e) {
      if (mine !== latest.current) return;
      setPlan(null);
      setError((e as Error).message);
    } finally {
      if (mine === latest.current) setBusy(false);
    }
  }

  async function confirm() {
    if (!file || !plan) return;
    setBusy(true);
    setError(null);
    try {
      // `plan.slug`, never the input box: the suggested name is accepted by
      // previewing it, and the box is still empty when it was taken from the
      // placeholder. Submitting `asSlug` there sent "" — so the server planned
      // the colliding name again and answered 409 while this button read
      // "Import <the free name>". The plan is what was reviewed, so the plan's
      // name is what lands.
      const result = await api.importAgent(file, plan.slug);
      setLanded(result);
      setFile(null);
      setPlan(null);
      onImported();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const blocked = plan ? plan.refusals.length > 0 || plan.collision !== "" : false;

  return (
    <div className="space-y-4">
      <label className="flex w-fit cursor-pointer items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-sm font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]">
        <Upload className="h-4 w-4" />
        Choose a bundle
        <input
          type="file"
          accept=".json,application/json"
          className="hidden"
          onChange={(e) => pick(e.target.files?.[0] ?? null)}
        />
      </label>

      {busy && (
        <p className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
          <Loader2 className="h-4 w-4 animate-spin" /> Reading the bundle…
        </p>
      )}

      {error && (
        <p role="alert" data-import-error className="text-sm text-[var(--color-red)]">
          {error}
        </p>
      )}

      {landed && (
        <div className="flex items-start gap-2 rounded-lg border border-[var(--color-primary)]/30 bg-[var(--color-primary)]/5 p-3 text-sm text-[var(--color-text)]">
          <Check className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-primary)]" />
          <span data-import-done>
            Imported <span className="font-mono">{landed.slug}</span>. {landed.next_step}
          </span>
        </div>
      )}

      {plan && (
        <div className="space-y-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
          <div>
            <p className="text-sm font-semibold text-[var(--color-text)]">{plan.name}</p>
            <p className="text-xs text-[var(--color-text-muted)]">
              {plan.description || plan.when_to_consult || "No description."}
            </p>
            <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">
              Version {plan.version} · exported {plan.exported_at || "at an unknown time"}{" "}
              as <span className="font-mono">{plan.source_slug}</span> ·{" "}
              {plan.files.length + plan.executables.length} files · {plan.total_bytes} bytes
            </p>
            <p data-agent-uid className="text-[11px] text-[var(--color-text-muted)]">
              Agent id <span className="font-mono">{plan.agent_uid}</span> — the same
              agent on every install that imported this bundle, whatever it is named
              there.
            </p>
            {plan.notes && (
              <p data-bundle-notes className="mt-1 text-[11px] text-[var(--color-text)]">
                “{plan.notes}”
              </p>
            )}
            {/* The label is not decoration: a handle rendered next to a download
                without one reads as provenance, and Condor verifies nothing
                here — it signs no bundle and has no keys to check one with. */}
            {(plan.author.name || plan.author.handle) && (
              <p data-bundle-author className="mt-1 text-[11px] text-[var(--color-text-muted)]">
                Author (unverified claim):{" "}
                <span className="text-[var(--color-text)]">
                  {plan.author.name || plan.author.handle}
                </span>
                {plan.author.name && plan.author.handle && ` (${plan.author.handle})`}
              </p>
            )}
          </div>

          {plan.unmet.length > 0 && (
            <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-2.5">
              <p className="mb-1 text-xs font-semibold text-[var(--color-text)]">
                {plan.unmet.length} thing{plan.unmet.length !== 1 ? "s" : ""} this install
                cannot give it
              </p>
              <p className="mb-2 text-[11px] text-[var(--color-text-muted)]">
                None of this stops the import — everything arrives muted anyway. It is
                what will not work until you fix it.
              </p>
              <ul className="space-y-0.5">
                {plan.unmet.map((u) => (
                  <li
                    key={`${u.kind}:${u.name}`}
                    data-unmet
                    className="text-[11px] text-[var(--color-text-muted)]"
                  >
                    <span className={u.blocking ? "text-amber-400" : ""}>{u.kind}</span>{" "}
                    <span className="font-mono text-[var(--color-text)]">{u.name}</span>
                    {u.have.length > 0 && <> — you have {u.have.join(", ")}</>}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {plan.executables.length > 0 && (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-2.5">
              <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-amber-400">
                <AlertTriangle className="h-3.5 w-3.5" />
                {plan.executables.length} executable file
                {plan.executables.length !== 1 ? "s" : ""}
              </p>
              <p className="mb-2 text-[11px] text-[var(--color-text-muted)]">
                A bundle from someone you do not trust is untrusted code: an imported
                routine runs inside Condor with no sandbox. These arrive muted — read
                each one before you switch it on.
              </p>
              <ul className="space-y-0.5">
                {plan.executables.map((f) => (
                  <li
                    key={f.path}
                    data-executable
                    className="flex items-center gap-1.5 font-mono text-[11px] text-[var(--color-text)]"
                  >
                    <FileCode2 className="h-3 w-3 shrink-0 text-amber-400" />
                    {f.path}
                    <span className="text-[var(--color-text-muted)]">{f.bytes} B</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <details>
            <summary className="cursor-pointer text-xs text-[var(--color-text-muted)]">
              {plan.files.length} other file{plan.files.length !== 1 ? "s" : ""}
            </summary>
            <ul className="mt-1 space-y-0.5">
              {plan.files.map((f) => (
                <li
                  key={f.path}
                  className="flex items-center gap-1.5 font-mono text-[11px] text-[var(--color-text-muted)]"
                >
                  <FileText className="h-3 w-3 shrink-0" />
                  {f.path}
                </li>
              ))}
            </ul>
          </details>

          <p className="text-[11px] text-[var(--color-text-muted)]">
            Lands as <span className="font-mono">{plan.slug}</span>, owned by you, with no
            server pin. Muted on arrival:{" "}
            {[...plan.mutes.routines, ...plan.mutes.skills].join(", ") || "nothing"}.
            {plan.agent_key_inherited &&
              ` Its model is not configured here, so it will use yours (${plan.agent_key}).`}
          </p>

          {plan.refusals.map((r) => (
            <p key={r} role="alert" data-refusal className="text-xs text-[var(--color-red)]">
              {r}
            </p>
          ))}

          {plan.collision && (
            <div className="space-y-1.5">
              <p role="alert" className="text-xs text-[var(--color-red)]">
                An agent called <span className="font-mono">{plan.collision}</span> already
                exists. Import never replaces one — give it another name.
              </p>
              <div className="flex items-center gap-2">
                <input
                  aria-label="Import as"
                  value={asSlug}
                  placeholder={plan.suggested_slug}
                  onChange={(e) => setAsSlug(e.target.value)}
                  className="w-56 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 font-mono text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
                />
                <button
                  onClick={() => file && review(file, asSlug || plan.suggested_slug)}
                  disabled={busy}
                  className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
                >
                  Check name
                </button>
              </div>
            </div>
          )}

          <button
            onClick={confirm}
            disabled={busy || blocked}
            className="flex items-center gap-1.5 rounded-lg border border-[var(--color-primary)]/40 bg-[var(--color-primary)]/10 px-3 py-1.5 text-sm font-semibold text-[var(--color-primary)] transition-all hover:bg-[var(--color-primary)]/20 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Upload className="h-4 w-4" />
            Import {plan.slug}
          </button>
        </div>
      )}
    </div>
  );
}
