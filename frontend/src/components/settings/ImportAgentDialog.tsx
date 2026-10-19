import {
  AlertTriangle,
  Check,
  FileCode2,
  FileText,
  Loader2,
  RefreshCw,
  Upload,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import {
  type AgentBundleFinding,
  type AgentImportPlan,
  type AgentUpdatePlan,
} from "@/lib/api";

import { fileSource, type BundleSource } from "./bundleSource";

/**
 * How each review category is introduced, most-decisive first (FEAT-144).
 *
 * The order is the server's and is not re-sorted here: `findings` arrives
 * ranked, and a second ordering in the client would be a second opinion about
 * what matters. This map only supplies the sentence, and the groups render in
 * the order the findings themselves first mention each category.
 */
const FINDING_COPY: Record<string, { title: string; blurb: string }> = {
  secret: {
    title: "Key-shaped values",
    blurb:
      "A value with the shape of a private key or a recovery phrase. Marked certain where the shape can be nothing else; the rest are also what a transaction hash looks like.",
  },
  endpoint: {
    title: "Hosts it reaches",
    blurb:
      "Where this agent's code and config dial out to. A loopback or private host is the normal case here; a public one is a stranger's server.",
  },
  code_run: {
    title: "Doors onto arbitrary code",
    blurb:
      "An imported routine runs inside Condor with no sandbox, and these lines run whatever string they are given.",
  },
  tool_steer: {
    title: "Tools that move money, named in its text",
    blurb:
      "Context, not an accusation — every strategy names its own executor tool. What distinguishes that from an instruction to trade for somebody else is the sentence around it, so each line is shown.",
  },
  risk_limits: {
    title: "Guardrails it brings with it",
    blurb:
      "These become this agent's limits on its first run. Nothing here clamps them to anything this install chose — read the figures.",
  },
};

/** The categories present, in the order the ranked findings first mention them. */
function categoriesOf(findings: AgentBundleFinding[]): string[] {
  const seen: string[] = [];
  for (const f of findings) if (!seen.includes(f.category)) seen.push(f.category);
  return seen;
}

/**
 * How each per-path verdict reads, and whether it needs the operator's eye
 * (FEAT-145).
 *
 * `kept` is the half that matters: those rows are the files where the update
 * did *not* do what upstream asked, because the bytes on disk are the
 * operator's and nobody else can reproduce them.
 */
const VERDICT_COPY: Record<string, { label: string; kept?: boolean }> = {
  added: { label: "new — arrives muted" },
  changed: { label: "replaced" },
  unchanged: { label: "unchanged" },
  local_only: { label: "your copy kept — upstream did not change it", kept: true },
  conflict: { label: "your copy kept — upstream changed it too", kept: true },
  removed: { label: "deleted" },
  conflict_removed: {
    label: "your copy kept — upstream dropped the file",
    kept: true,
  },
};

/** The verdicts worth showing first: everything that is not `unchanged`. */
const VERDICT_ORDER = [
  "conflict",
  "conflict_removed",
  "local_only",
  "changed",
  "added",
  "removed",
  "unchanged",
];

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
 * dialog renders is what the write does — findings included, which is why the
 * list here cannot differ from the one the import recorded (FEAT-144).
 */
export function ImportAgentDialog({
  onImported,
  source,
  provenance,
}: {
  onImported: () => void;
  /**
   * Where the bundle comes from. Omitted means "ask the operator for a file",
   * which is the panel's own case; the hub passes a listing-backed source and
   * this dialog previews it on sight instead of showing a picker.
   */
  source?: BundleSource;
  /**
   * Rendered above the plan. The hub puts its badge, its author attestation
   * and the trust sentence here — facts about where this came from, which is
   * exactly the part a file on disk cannot have.
   */
  provenance?: ReactNode;
}) {
  const [picked, setPicked] = useState<BundleSource | null>(null);
  const active = source ?? picked;
  const [plan, setPlan] = useState<AgentImportPlan | null>(null);
  const [asSlug, setAsSlug] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [landed, setLanded] = useState<AgentImportPlan | null>(null);
  /**
   * The update diff, once the operator has asked to see it.
   *
   * A separate piece of state and not a mode flag: the import plan stays on
   * screen underneath, because the identity, the author claim and the review
   * findings are the same facts whichever verb ends up running.
   */
  const [update, setUpdate] = useState<AgentUpdatePlan | null>(null);
  const [applied, setApplied] = useState<AgentUpdatePlan | null>(null);
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

  function reset() {
    setPlan(null);
    setLanded(null);
    setUpdate(null);
    setApplied(null);
    setError(null);
    setAsSlug("");
  }

  async function pick(chosen: File | null) {
    setPicked(chosen ? fileSource(chosen) : null);
    reset();
    if (!chosen) return;
    await review(fileSource(chosen), "");
  }

  // A supplied source needs no picker, so there is nothing for the operator to
  // press: the review starts on sight. Keyed on `source.key` so a different
  // listing re-previews and the same one does not loop.
  const sourceKey = source?.key ?? "";
  useEffect(() => {
    if (!source) return;
    reset();
    void review(source, "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceKey]);

  async function review(from: BundleSource, slug: string) {
    const mine = ++latest.current;
    setBusy(true);
    setError(null);
    setUpdate(null);
    try {
      const previewed = await from.preview(slug);
      if (mine !== latest.current) return;
      // The two verbs land in their own state, so neither renderer can be
      // handed the other's shape.
      if (previewed.verb === "update") {
        setPlan(null);
        setUpdate(previewed.plan);
      } else {
        setPlan(previewed.plan);
      }
    } catch (e) {
      if (mine !== latest.current) return;
      setPlan(null);
      setError((e as Error).message);
    } finally {
      if (mine === latest.current) setBusy(false);
    }
  }

  /** The other verb: what replacing the installed agent's library would do. */
  async function reviewUpdate(from: BundleSource, slug: string) {
    const mine = ++latest.current;
    setBusy(true);
    setError(null);
    try {
      const previewed = await from.previewUpdate(slug);
      if (mine !== latest.current) return;
      setUpdate(previewed);
    } catch (e) {
      if (mine !== latest.current) return;
      setUpdate(null);
      setError((e as Error).message);
    } finally {
      if (mine === latest.current) setBusy(false);
    }
  }

  async function confirmUpdate() {
    if (!active || !update) return;
    setBusy(true);
    setError(null);
    try {
      // `update.slug`, which is the agent the diff on screen was computed
      // against — never a name typed since.
      const result = await active.applyUpdate(update.slug);
      setApplied(result);
      setPicked(null);
      setPlan(null);
      setUpdate(null);
      onImported();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!active || !plan) return;
    setBusy(true);
    setError(null);
    try {
      // `plan.slug`, never the input box: the suggested name is accepted by
      // previewing it, and the box is still empty when it was taken from the
      // placeholder. Submitting `asSlug` there sent "" — so the server planned
      // the colliding name again and answered 409 while this button read
      // "Import <the free name>". The plan is what was reviewed, so the plan's
      // name is what lands.
      const result = await active.install(plan.slug);
      setLanded(result);
      setPicked(null);
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
      {!source && (
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
      )}

      {/* Above the plan, not inside it: where this came from is the first
          question, and the answer is the same whichever verb follows. */}
      {provenance}

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

      {applied && (
        <div className="flex items-start gap-2 rounded-lg border border-[var(--color-primary)]/30 bg-[var(--color-primary)]/5 p-3 text-sm text-[var(--color-text)]">
          <Check className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-primary)]" />
          <span data-update-done>
            Updated <span className="font-mono">{applied.slug}</span> to version{" "}
            {applied.version}. {applied.next_step}
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

          {/* The review of what the files themselves carry, beside the file
              list rather than behind a disclosure: it is the thing the operator
              is here to read. Grouped by category, each finding with its path
              and line, and no verdict anywhere — the server reports and refuses
              nothing on this side. */}
          {plan.findings.length > 0 && (
            <div
              data-findings
              className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-2.5"
            >
              <p className="mb-1 text-xs font-semibold text-[var(--color-text)]">
                The review found {plan.findings.length} thing
                {plan.findings.length !== 1 ? "s" : ""} to look at
              </p>
              <p className="mb-2 text-[11px] text-[var(--color-text-muted)]">
                A bundle from someone you do not trust is untrusted code, and there is
                no sandbox here. Every file it would write is named on this screen
                before anything is written, and this is what a scan of their contents
                found. It warns; it does not prevent.
              </p>
              <div className="space-y-2">
                {categoriesOf(plan.findings).map((category) => {
                  const rows = plan.findings.filter((f) => f.category === category);
                  const copy = FINDING_COPY[category];
                  return (
                    <div key={category} data-finding-group={category}>
                      <p className="text-[11px] font-semibold text-[var(--color-text)]">
                        {copy?.title ?? category}{" "}
                        <span className="font-normal text-[var(--color-text-muted)]">
                          ({rows.length})
                        </span>
                      </p>
                      {copy && (
                        <p className="text-[10px] text-[var(--color-text-muted)]">
                          {copy.blurb}
                        </p>
                      )}
                      <ul className="mt-0.5 space-y-0.5">
                        {rows.map((f) => (
                          <li
                            key={`${f.path}:${f.start}:${f.label}`}
                            data-finding={f.category}
                            className="text-[11px] text-[var(--color-text-muted)]"
                          >
                            <span
                              className={
                                f.certain
                                  ? "font-semibold text-[var(--color-red)]"
                                  : "text-amber-400"
                              }
                            >
                              {f.label}
                              {f.certain && " (certain)"}
                            </span>{" "}
                            <span className="font-mono text-[var(--color-text)]">
                              {f.path}:{f.line}
                            </span>
                            {f.context && (
                              <>
                                {" — "}
                                <span className="font-mono text-[10px]">{f.context}</span>
                              </>
                            )}
                          </li>
                        ))}
                      </ul>
                    </div>
                  );
                })}
              </div>
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
              {plan.is_update ? (
                /* The same agent, newer. Offering only a rename here is what
                   left the new version sitting beside the old one with the
                   memory forked, so the update verb is offered first and the
                   rename stays available underneath (FEAT-145). */
                <div data-update-offer className="space-y-1.5">
                  <p className="text-xs text-[var(--color-text)]">
                    This is a newer version of{" "}
                    <span className="font-mono">{plan.collision}</span>, the agent you
                    already have — version {plan.version} against the one installed.
                    Updating keeps its memory, its config, its mutes and anything you
                    edited here.
                  </p>
                  <button
                    onClick={() => active && reviewUpdate(active, plan.collision)}
                    disabled={busy}
                    className="flex items-center gap-1.5 rounded-lg border border-[var(--color-primary)]/40 bg-[var(--color-primary)]/10 px-3 py-1.5 text-sm font-semibold text-[var(--color-primary)] transition-all hover:bg-[var(--color-primary)]/20 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <RefreshCw className="h-4 w-4" />
                    Review the changes
                  </button>
                  <p className="text-[11px] text-[var(--color-text-muted)]">
                    Or import it as a second agent under another name:
                  </p>
                </div>
              ) : (
                <p role="alert" className="text-xs text-[var(--color-red)]">
                  An agent called <span className="font-mono">{plan.collision}</span>{" "}
                  already exists. Import never replaces one — give it another name.
                </p>
              )}
              <div className="flex items-center gap-2">
                <input
                  aria-label="Import as"
                  value={asSlug}
                  placeholder={plan.suggested_slug}
                  onChange={(e) => setAsSlug(e.target.value)}
                  className="w-56 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 font-mono text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
                />
                <button
                  onClick={() => active && review(active, asSlug || plan.suggested_slug)}
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

      {update && (
        <div
          data-update-plan
          className="space-y-3 rounded-lg border border-[var(--color-primary)]/30 bg-[var(--color-surface)] p-3"
        >
          <div>
            <p className="text-sm font-semibold text-[var(--color-text)]">
              Updating <span className="font-mono">{update.slug}</span> — version{" "}
              {update.installed_version || "?"} → {update.version}
            </p>
            <p className="text-[11px] text-[var(--color-text-muted)]">
              Its memory, config, mutes, journals and session output are untouched.
              Nothing is started, and every routine that changes has to be read and
              allowed again before it can run.
            </p>
          </div>

          {update.blockers.map((b) => (
            <p
              key={b.code}
              role="alert"
              data-update-blocker={b.code}
              className="text-xs text-[var(--color-red)]"
            >
              {b.message}
            </p>
          ))}

          {update.warnings.map((w) => (
            <p
              key={w.code}
              data-update-warning={w.code}
              className="text-[11px] text-amber-400"
            >
              {w.message}
            </p>
          ))}

          {update.blockers.length === 0 && (
            <>
              <ul className="space-y-0.5">
                {[...update.files]
                  .sort(
                    (a, b) =>
                      VERDICT_ORDER.indexOf(a.verdict) -
                        VERDICT_ORDER.indexOf(b.verdict) ||
                      a.path.localeCompare(b.path),
                  )
                  .map((f) => {
                    const copy = VERDICT_COPY[f.verdict];
                    return (
                      <li
                        key={f.path}
                        data-update-file={f.verdict}
                        className="flex items-center gap-1.5 text-[11px]"
                      >
                        {f.executable ? (
                          <FileCode2 className="h-3 w-3 shrink-0 text-amber-400" />
                        ) : (
                          <FileText className="h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
                        )}
                        <span className="font-mono text-[var(--color-text)]">
                          {f.path}
                        </span>
                        <span
                          className={
                            copy?.kept
                              ? "text-amber-400"
                              : "text-[var(--color-text-muted)]"
                          }
                        >
                          {copy?.label ?? f.verdict}
                        </span>
                      </li>
                    );
                  })}
              </ul>

              <p className="text-[11px] text-[var(--color-text-muted)]">
                Muted on arrival:{" "}
                {[...update.mutes.routines, ...update.mutes.skills].join(", ") ||
                  "nothing new"}
                . The previous bytes of every replaced or deleted file are kept
                beside the agent, out of its library.
              </p>

              <button
                onClick={confirmUpdate}
                disabled={busy}
                className="flex items-center gap-1.5 rounded-lg border border-[var(--color-primary)]/40 bg-[var(--color-primary)]/10 px-3 py-1.5 text-sm font-semibold text-[var(--color-primary)] transition-all hover:bg-[var(--color-primary)]/20 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <RefreshCw className="h-4 w-4" />
                Update {update.slug}
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
