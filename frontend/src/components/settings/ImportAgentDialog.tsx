import {
  AlertTriangle,
  Check,
  FileCode2,
  FileText,
  Loader2,
  RefreshCw,
  Upload,
} from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";

import {
  type AgentBundleFinding,
  type AgentImportPlan,
  type AgentUpdatePlan,
} from "@/lib/api";

import { stripMarkdown } from "@/lib/formatters";

import { fileSource, type BundleSource } from "./bundleSource";
import { ScreenFade } from "./ScreenFade";

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
 * operator's and nobody else can reproduce them. `added` has no static entry
 * here — its label depends on the plan's own mute lists (see `addedLabel`),
 * because a hub update's skills and routines do not arrive muted (FEAT-146,
 * widened to routines in FEAT-150).
 */
const VERDICT_COPY: Record<string, { label: string; kept?: boolean }> = {
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

/**
 * `routines/<name>.py`'s name, or `null` when the path is not a routine —
 * mirroring the server's `_routine_name` exactly, so `addedLabel` and the
 * kept-local check below can't disagree with it about what counts as one.
 */
function routineNameOf(path: string): string | null {
  const parts = path.split("/");
  if (parts.length === 2 && parts[0] === "routines" && path.endsWith(".py")) {
    return parts[1].slice(0, -".py".length);
  }
  return null;
}

/**
 * What an `added` row should say, per the plan's own mute lists rather than
 * assuming every new file lands switched off (FEAT-146, widened to routines
 * in FEAT-150): a hub update's skills and routines are not muted, so a
 * plain file's path is checked against what actually got muted instead of
 * being told a blanket story. An unmuted routine still isn't "ready to run"
 * the way an unmuted skill is on a plain import — it needs its content
 * allowed (FEAT-147) — except on a hub update (`hubReviewed`), where that
 * allow already happened: the hub's review covers execution too.
 */
function addedLabel(
  path: string,
  mutes: { routines: string[]; skills: string[] },
  hubReviewed: boolean,
): string {
  const routineName = routineNameOf(path);
  if (routineName !== null) {
    if (mutes.routines.includes(routineName)) return "new — arrives muted";
    return hubReviewed ? "new — ready to run" : "new — needs your allow";
  }
  const parts = path.split("/");
  if (parts.length > 2 && parts[0] === "skills") {
    return mutes.skills.includes(parts[1]) ? "new — arrives muted" : "new — enabled on arrival";
  }
  return "new";
}

/**
 * The sentence that follows "Nothing is started" on an update screen.
 *
 * A hub update's auto-allow only ever covers the bytes it just wrote —
 * `apply_update` on the server excludes exactly the kept-local verdicts
 * (`local_only`/`conflict`/`conflict_removed`) from granting consent,
 * because those bytes are the operator's own edit, never the hub's
 * reviewed text (Policy 3). So `hubReviewed` alone is not enough to say "no
 * further allow needed" — a routine kept local still needs the ordinary
 * per-file gate, and the copy has to say so instead of promising otherwise.
 */
function updateConsentNote(hubReviewed: boolean, keptLocalRoutines: string[]): string {
  if (!hubReviewed) {
    return ", and every routine that changes has to be read and allowed again before it can run.";
  }
  if (keptLocalRoutines.length === 0) {
    return ", and the hub's own review already covers every routine this update changes — no further allow needed before it can run.";
  }
  const n = keptLocalRoutines.length;
  return (
    ", and the hub's own review covers every routine this update replaces" +
    ` — but ${keptLocalRoutines.join(", ")} kept ${n === 1 ? "its" : "their"} own copy` +
    ` and still ${n === 1 ? "needs" : "need"} your allow before ${n === 1 ? "it" : "they"} can run.`
  );
}

/**
 * The one primary and one secondary button shape, used everywhere this
 * dialog and its success screen need a button — so the review's Import, the
 * wizard's Continue and the success screen's Start chatting are never three
 * different-looking "do the main thing" buttons.
 */
function PrimaryButton({
  className = "",
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...rest}
      className={`flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-3 py-1.5 text-sm font-semibold text-[var(--on-primary)] transition-colors hover:bg-[var(--color-primary-hover)] disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
    >
      {children}
    </button>
  );
}

function SecondaryButton({
  className = "",
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...rest}
      className={`rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-sm font-semibold text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
    >
      {children}
    </button>
  );
}

/** "Step 1 of 2" / "Step 2 of 2" — enough to tell the wizard has two screens. */
function StepIndicator({ step }: { step: 1 | 2 }) {
  return (
    <p className="flex items-center gap-1.5 text-[11px] font-semibold text-[var(--color-text-muted)]">
      <span className="flex gap-1">
        {([1, 2] as const).map((n) => (
          <span
            key={n}
            className={`h-1.5 w-1.5 rounded-full ${
              n === step ? "bg-[var(--color-primary)]" : "bg-[var(--color-border)]"
            }`}
          />
        ))}
      </span>
      Step {step} of 2
    </p>
  );
}

/**
 * The plain-language risk the operator is actually agreeing to, on step 2
 * (FEAT-15x). Amber only when there is something to warn about — a bundle
 * with no executable file writes config and skill text, nothing that runs.
 */
function ConsentDisclaimer({ plan }: { plan: AgentImportPlan }) {
  const n = plan.executables.length;
  if (n === 0) {
    return (
      <p className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-2.5 text-xs leading-relaxed text-[var(--color-text)]">
        This install writes {plan.files.length} file{plan.files.length !== 1 ? "s" : ""} and
        runs no code of its own.
        {plan.hub_reviewed && " The hub has already reviewed this exact bundle."}
      </p>
    );
  }
  return (
    <p className="flex items-start gap-1.5 rounded-md border border-amber-500/30 bg-amber-500/5 p-2.5 text-xs leading-relaxed text-[var(--color-text)]">
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400" />
      <span>
        This agent brings {n} executable file{n !== 1 ? "s" : ""}, and Condor runs them on
        this machine with no sandbox.{" "}
        {plan.hub_reviewed
          ? "The hub's own review is the only check that has happened on them — read them anyway."
          : "Nobody but you will have reviewed them before they run."}
      </span>
    </p>
  );
}

/** The consent checkbox's wording — explicit about code execution once there
 *  is code to execute, so the box is never a euphemism for what it grants. */
function consentLabel(plan: AgentImportPlan): string {
  return plan.executables.length > 0
    ? "I understand this agent can run code on my machine, and I want to install it."
    : "I have reviewed what this agent brings in and I want to install it.";
}

/**
 * The update screen's twin of `consentLabel`.
 *
 * Only a hub update reaches this: `apply_update` records Botcamp's terms
 * acceptance for every hub update, the same write a fresh install only
 * performs after this same checkbox is ticked (`install_from_hub`'s
 * `post_consent` call does not distinguish the two verbs). Without this gate
 * an update recorded that acceptance while the screen never showed the terms
 * or asked for agreement.
 */
function updateConsentLabel(update: AgentUpdatePlan): string {
  const runsCode = update.files.some(
    (f) => f.executable && f.verdict !== "unchanged" && f.verdict !== "removed",
  );
  return runsCode
    ? "I understand this update can run code on my machine, and I want to apply it."
    : "I have reviewed what this update brings in and I want to apply it.";
}

/**
 * One collapsed-by-default review section (FEAT-151): closed on open, its
 * one-line summary already stating the count and the headline fact so the
 * owner can decide whether to open it without being made to first.
 */
function ReviewSection({
  summary,
  children,
  ...rest
}: {
  summary: ReactNode;
  children: ReactNode;
} & Record<`data-${string}`, boolean | string>) {
  return (
    <details
      {...rest}
      className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-2.5"
    >
      <summary className="cursor-pointer text-xs font-semibold text-[var(--color-text)]">
        {summary}
      </summary>
      <div className="mt-2 space-y-2">{children}</div>
    </details>
  );
}

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
  onResult,
  source,
  provenance,
  stickyAction = false,
  onOpenAgent,
  onDismiss,
}: {
  onImported: () => void;
  /**
   * Reports what the success screen's heading would say, for a host that
   * renders its own modal chrome around this dialog (the Hub's review
   * modal, titled "Review before installing" until something lands).
   * Fires with `null` whenever the dialog is back to reviewing — a fresh
   * pick, a re-preview under another name — so the host's heading reverts.
   */
  onResult?: (result: { verb: "imported" | "updated"; slug: string } | null) => void;
  /**
   * Where the bundle comes from. Omitted means "ask the operator for a file",
   * which is the panel's own case; the hub passes a listing-backed source and
   * this dialog previews it on sight instead of showing a picker.
   */
  source?: BundleSource;
  /**
   * Rendered above the plan, and nowhere once it lands: the trust sentence is
   * about the decision, not the outcome, so it never follows the screen past
   * the review. The hub puts its badge, its author attestation and the trust
   * sentence here — facts about where this came from, which is exactly the
   * part a file on disk cannot have.
   */
  provenance?: ReactNode;
  /**
   * Keep the Import button in view while the review scrolls — for a host that
   * scrolls the review in a box of its own (the Hub's review modal). The file
   * panel keeps it inline, after the review.
   */
  stickyAction?: boolean;
  /**
   * Offered as a next action once the agent has landed — "Start chatting",
   * dropping the operator straight into a chat with the agent that just
   * landed, which is what the moment calls for rather than its settings
   * page. Omitted (the file panel's case) leaves the success screen with no
   * such button; a host with somewhere to send the operator passes this
   * instead of reaching into the dialog's own `landed`/`applied` state for
   * the slug.
   */
  onOpenAgent?: (slug: string) => void;
  /**
   * Offered as "Close" on the success screen, for a host that renders this
   * dialog inside a dismissable shell (the Hub's review modal). Omitted
   * where there is nothing to dismiss.
   */
  onDismiss?: () => void;
}) {
  const [picked, setPicked] = useState<BundleSource | null>(null);
  const active = source ?? picked;
  const [plan, setPlan] = useState<AgentImportPlan | null>(null);
  /**
   * The hub catalogue's own long-form text, when this source has one — a
   * different field from `plan.description`, which is the AGENT.md's short
   * routing blurb, not a shortened version of the same sentence. Unset for a
   * plain file import, which has no catalogue entry to carry one from.
   */
  const [fullDescription, setFullDescription] = useState("");
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
   * The explicit consent the operator has to tick before Install is enabled —
   * ticked for exactly one plan, so a rename (`review()` again, under
   * "Check name") or a fresh pick asks for it again rather than carrying a
   * tick from a plan that is no longer the one on screen.
   */
  const [consented, setConsented] = useState(false);
  /**
   * Which screen of the review wizard is on top — set back to 1 by `reset()`
   * and by every fresh `review()` call, so a rename or a reopened dialog
   * never leaves the operator stranded on the confirm screen of a plan that
   * is no longer the one on top.
   */
  const [step, setStep] = useState<1 | 2>(1);
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
    setFullDescription("");
    setLanded(null);
    setUpdate(null);
    setApplied(null);
    setError(null);
    setAsSlug("");
    setConsented(false);
    setStep(1);
    onResult?.(null);
  }

  /** Abandon the review: the file panel goes back to its picker, the hub's
   *  modal closes — whichever this host actually offers. */
  function cancel() {
    reset();
    setPicked(null);
    onDismiss?.();
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
    setConsented(false);
    setStep(1);
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
        setFullDescription(previewed.fullDescription ?? "");
        // A hub source whose account already consented to this exact
        // version: pre-tick step 2's box rather than asking the owner to
        // read the same terms a second time.
        setConsented(Boolean(previewed.alreadyConsented));
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
    setConsented(false);
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
      onResult?.({ verb: "updated", slug: result.slug });
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
      onResult?.({ verb: "imported", slug: result.slug });
      onImported();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const blocked = plan ? plan.refusals.length > 0 || plan.collision !== "" : false;

  // The version every hub-flow surface shows (CORR-1019): the Botcamp
  // listing number the operator actually picked, never the exporting
  // install's own internal counter (`version`/`installed_version`) — those
  // two are different numbering systems and only line up by coincidence.
  // A plain file import/update has no hub number at all, so it keeps
  // showing `version` exactly as it always has.
  const planDisplayVersion = plan && plan.hub_reviewed ? plan.hub_version : plan?.version;
  const updateDisplayVersion =
    update && update.hub_reviewed ? update.hub_version : update?.version;
  const updateDisplayInstalledVersion =
    update && update.hub_reviewed
      ? update.installed_hub_version
      : update?.installed_version;

  // Everything this bundle carries under `routines/` — shared by the summary
  // counts line, the unmet reassurance below, and the files section.
  const carriedRoutines = plan
    ? plan.executables
        .filter((f) => f.path.startsWith("routines/") && f.path.endsWith(".py"))
        .map((f) => f.path.split("/")[1].slice(0, -".py".length))
    : [];
  // `loops/<name>/loop.md` is the one non-`.py` path shape that names a loop.
  const carriedLoops = plan
    ? plan.files
        .filter((f) => {
          const parts = f.path.split("/");
          return parts.length === 3 && parts[0] === "loops" && parts[2] === "loop.md";
        })
        .map((f) => f.path.split("/")[1])
    : [];
  const declaredSkills = plan
    ? [...plan.requires.skills.carried, ...plan.requires.skills.inherited]
    : [];
  const planFileCount = plan ? plan.files.length + plan.executables.length : 0;
  // The two categories the owner reads as their own section rather than a
  // panel in the catch-all grid (FEAT-151); everything else stays grouped.
  const hostFindings = plan ? plan.findings.filter((f) => f.category === "endpoint") : [];
  const guardrailFindings = plan
    ? plan.findings.filter((f) => f.category === "risk_limits")
    : [];
  const otherFindings = plan
    ? plan.findings.filter((f) => f.category !== "endpoint" && f.category !== "risk_limits")
    : [];

  // Nothing this update would do: the pinned version matches what's
  // installed and every file already carries the bundle's own bytes, so
  // there is no file to write and nothing to mute. `files.every` on `[]`
  // is vacuously true, but an update plan always carries at least AGENT.md.
  //
  // Never true for a hub update (`hub_reviewed`): `apply_update` grants
  // execution consent for every `unchanged` routine too, and `install_from_hub`
  // stamps the hub source afterwards either way — both run even when no file
  // differs. A file import that matches the hub's bytes exactly would
  // otherwise see only "Close" here and have no way to pick up that consent.
  const noChanges = update
    ? !update.hub_reviewed &&
      update.blockers.length === 0 &&
      update.version === update.installed_version &&
      update.files.every((f) => f.verdict === "unchanged")
    : false;

  // Routines this update left as the operator's own edit rather than
  // replacing with the bundle's reviewed bytes — see `updateConsentNote`.
  const keptLocalRoutines = update
    ? update.files
        .filter((f) => VERDICT_COPY[f.verdict]?.kept)
        .map((f) => routineNameOf(f.path))
        .filter((name): name is string => name !== null)
    : [];

  // Coarse enough to animate only an actual screen change: re-previewing
  // under a new name stays "review" throughout, so it swaps instantly.
  const landedOrApplied = landed ?? applied;
  const phase = landedOrApplied
    ? "success"
    : update
      ? "update"
      : plan
        ? `review-${step}`
        : busy
          ? "busy"
          : "idle";

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

      <ScreenFade phaseKey={phase}>
        <div className="space-y-4">
          {/* Above the plan, not inside it: where this came from is the first
              question, and the answer is the same whichever verb follows.
              Gone once it lands — the trust note is about the decision, not
              the outcome it led to. */}
          {!landedOrApplied && provenance}

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

          {landedOrApplied && (
            <SuccessScreen
              landed={landed}
              applied={applied}
              onOpenAgent={onOpenAgent}
              onDismiss={onDismiss}
            />
          )}

      {plan && (
        <div className="space-y-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
          <StepIndicator step={step} />

          {/* Step 1 — what you're installing: the identity and every
              collapsible fact about it, nothing to decide yet. Step 2 is
              below, once this whole block is skipped. */}
          {step === 1 && (
            <>
          {/* The short, plain summary first: what the owner gets, stated as
              counts rather than prose about the hub. Everything else below
              is detail, collapsed until asked for. */}
          <div className="space-y-1">
            <p className="text-sm font-semibold text-[var(--color-text)]">{plan.name}</p>
            {/* The tagline first — the AGENT.md's own short routing blurb —
                then the hub's full write-up below it, when this source has
                one. Two different fields (BUG-2), so neither is dropped for
                the other. */}
            <p className="text-xs font-medium text-[var(--color-text-muted)]">
              {stripMarkdown(plan.description || plan.when_to_consult) || "No description."}
            </p>
            {fullDescription &&
              stripMarkdown(fullDescription) !==
                stripMarkdown(plan.description || plan.when_to_consult) && (
                <p className="text-xs text-[var(--color-text-muted)]">
                  {stripMarkdown(fullDescription)}
                </p>
              )}
            {plan.notes && (
              <p data-bundle-notes className="text-[11px] text-[var(--color-text)]">
                “{plan.notes}”
              </p>
            )}
            <p className="pt-0.5 text-[11px] text-[var(--color-text-muted)]">
              {plan.requires.tools.length} tool{plan.requires.tools.length !== 1 ? "s" : ""} ·{" "}
              {declaredSkills.length} skill{declaredSkills.length !== 1 ? "s" : ""} ·{" "}
              {carriedRoutines.length} routine{carriedRoutines.length !== 1 ? "s" : ""} ·{" "}
              {carriedLoops.length} loop{carriedLoops.length !== 1 ? "s" : ""} ·{" "}
              {planFileCount} file{planFileCount !== 1 ? "s" : ""} · {plan.total_bytes} bytes
            </p>
          </div>

          {plan.unmet.length > 0 && (
            <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-2.5">
              <p className="mb-1 text-xs font-semibold text-[var(--color-text)]">
                {plan.unmet.length} thing{plan.unmet.length !== 1 ? "s" : ""} this install
                cannot give it
              </p>
              <p className="mb-2 text-[11px] text-[var(--color-text-muted)]">
                {/* A hub import's routines do not arrive muted any more than
                    its skills do (FEAT-150), so the reassurance is read off
                    `plan.mutes` for both rather than assuming routines are
                    always the muted half. */}
                {(() => {
                  const routinesMuted = carriedRoutines.every((r) =>
                    plan.mutes.routines.includes(r),
                  );
                  const skillsMuted = plan.requires.skills.carried.every((s) =>
                    plan.mutes.skills.includes(s),
                  );
                  if (routinesMuted && skillsMuted) {
                    return "None of this stops the import — everything arrives muted anyway. It is what will not work until you fix it.";
                  }
                  if (!routinesMuted && !skillsMuted) {
                    return plan.hub_reviewed
                      ? "None of this stops the import. Nothing here is muted — its skills and routines land ready to use, and the hub's own review already covers execution, so nothing further is needed before they run. This is what will not work until you fix it."
                      : "None of this stops the import. Nothing here is muted — its skills and routines land ready to use, though every routine still needs you to allow it before it runs. This is what will not work until you fix it.";
                  }
                  return routinesMuted
                    ? "None of this stops the import. Its routines arrive muted, but this agent's skills do not — they land ready to use. This is what will not work until you fix it."
                    : `None of this stops the import. Its routines arrive unmuted (${plan.hub_reviewed ? "and already have the hub's execution consent" : "still pending your allow before they run"}), but its skills are muted. This is what will not work until you fix it.`;
                })()}
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

          {/* Everything else, collapsed by default: a one-line header that
              already states the count and the headline fact, so deciding
              whether to open a section needs no click first. */}
          <ReviewSection
            summary={`What it can do — ${plan.requires.tools.length} tool${plan.requires.tools.length !== 1 ? "s" : ""}, ${declaredSkills.length} skill${declaredSkills.length !== 1 ? "s" : ""}, all enabled on arrival`}
          >
            {plan.requires.tools.length > 0 && (
              <div>
                <p className="text-[11px] font-semibold text-[var(--color-text)]">Tools</p>
                <ul className="mt-0.5 flex flex-wrap gap-1">
                  {plan.requires.tools.map((t) => (
                    <li
                      key={t}
                      className="rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-0.5 font-mono text-[11px] text-[var(--color-text)]"
                    >
                      {t}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {declaredSkills.length > 0 && (
              <div>
                <p className="text-[11px] font-semibold text-[var(--color-text)]">Skills</p>
                <ul className="mt-0.5 flex flex-wrap gap-1">
                  {plan.requires.skills.carried.map((s) => (
                    <li
                      key={`carried:${s}`}
                      data-skill="carried"
                      className="rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-0.5 font-mono text-[11px] text-[var(--color-text)]"
                    >
                      {s}
                    </li>
                  ))}
                  {plan.requires.skills.inherited.map((s) => (
                    <li
                      key={`inherited:${s}`}
                      data-skill="inherited"
                      className="rounded-full border border-dashed border-[var(--color-border)] px-2 py-0.5 font-mono text-[11px] text-[var(--color-text-muted)]"
                    >
                      {s} (shared)
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {plan.requires.tools.length === 0 && declaredSkills.length === 0 && (
              <p className="text-[11px] text-[var(--color-text-muted)]">
                Declares no tools or skills of its own.
              </p>
            )}
          </ReviewSection>

          <ReviewSection
            summary={`Files it writes — ${plan.executables.length} executable, ${plan.files.length} other`}
          >
            {plan.executables.length > 0 && (
              <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-2.5">
                <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-amber-400">
                  <AlertTriangle className="h-3.5 w-3.5" />
                  {plan.executables.length} executable file
                  {plan.executables.length !== 1 ? "s" : ""}
                </p>
                <p className="mb-2 text-[11px] text-[var(--color-text-muted)]">
                  {plan.hub_reviewed
                    ? "An imported routine runs inside Condor with no sandbox. The hub already reviewed this exact content, so these arrive with execution consent already granted — read them anyway."
                    : "A bundle from someone you do not trust is untrusted code: an imported routine runs inside Condor with no sandbox. These arrive muted — read each one before you switch it on."}
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
            <div>
              <p className="mb-1 text-[11px] font-semibold text-[var(--color-text)]">
                {plan.files.length} other file{plan.files.length !== 1 ? "s" : ""}
              </p>
              <ul className="space-y-0.5">
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
            </div>
          </ReviewSection>

          {hostFindings.length > 0 && (
            <ReviewSection
              data-hosts
              summary={`Hosts it reaches — ${hostFindings.length} host${hostFindings.length !== 1 ? "s" : ""}${
                hostFindings.filter((f) => f.label === "public").length > 0
                  ? `, ${hostFindings.filter((f) => f.label === "public").length} public`
                  : ""
              }`}
            >
              <p className="text-[11px] text-[var(--color-text-muted)]">
                {FINDING_COPY.endpoint.blurb}
              </p>
              <ul className="space-y-0.5">
                {hostFindings.map((f) => (
                  <li
                    key={`${f.path}:${f.start}:${f.label}`}
                    data-finding={f.category}
                    className="text-[11px] text-[var(--color-text-muted)]"
                  >
                    <span
                      className={
                        f.label === "public"
                          ? "font-semibold text-[var(--color-red)]"
                          : "text-[var(--color-text-muted)]"
                      }
                    >
                      {f.label}
                    </span>{" "}
                    <span className="font-mono text-[var(--color-text)]">
                      {f.path}:{f.line}
                    </span>
                  </li>
                ))}
              </ul>
            </ReviewSection>
          )}

          {guardrailFindings.length > 0 && (
            <ReviewSection
              data-guardrails
              summary={`Guardrails it brings with it — ${guardrailFindings.length} limit${guardrailFindings.length !== 1 ? "s" : ""}`}
            >
              <p className="text-[11px] text-[var(--color-text-muted)]">
                {FINDING_COPY.risk_limits.blurb}
              </p>
              <ul className="space-y-0.5">
                {guardrailFindings.map((f) => (
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
            </ReviewSection>
          )}

          {/* The remaining categories, grouped as before: each one a panel
              with its path and line, and no verdict anywhere — the server
              reports and refuses nothing on this side. */}
          {otherFindings.length > 0 && (
            <ReviewSection
              summary={`Other things the scan found — ${otherFindings.length} finding${otherFindings.length !== 1 ? "s" : ""}`}
            >
              <div data-findings>
                <p className="mb-2 text-[11px] text-[var(--color-text-muted)]">
                  A bundle from someone you do not trust is untrusted code, and there is
                  no sandbox here. Every file it would write is named on this screen
                  before anything is written, and this is what a scan of their contents
                  found. It warns; it does not prevent.
                </p>
                {/* A grid of self-contained panels rather than one tall column:
                    the width is there to show several groups side by side, and
                    each panel scrolls on its own. */}
                <div className="@container">
                  <div className="grid grid-cols-1 gap-2 @md:grid-cols-2 @2xl:grid-cols-3">
                  {categoriesOf(otherFindings).map((category) => {
                    const rows = otherFindings.filter((f) => f.category === category);
                    const copy = FINDING_COPY[category];
                    return (
                      <div
                        key={category}
                        data-finding-group={category}
                        className="min-w-0 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] p-2"
                      >
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
                        <ul className="mt-0.5 max-h-48 space-y-0.5 overflow-y-auto pr-0.5">
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
              </div>
            </ReviewSection>
          )}

          <ReviewSection summary={`Where it lands — lands as ${plan.slug}`}>
            <p className="text-[11px] text-[var(--color-text-muted)]">
              Lands as <span className="font-mono">{plan.slug}</span>, owned by you, with no
              server pin. Muted on arrival:{" "}
              {[...plan.mutes.routines, ...plan.mutes.skills].join(", ") || "nothing"}.
              {plan.agent_key_inherited &&
                ` Its model is not configured here, so it will use yours (${plan.agent_key}).`}
            </p>
            <p className="text-[11px] text-[var(--color-text-muted)]">
              Version {planDisplayVersion} · exported {plan.exported_at || "at an unknown time"}{" "}
              as <span className="font-mono">{plan.source_slug}</span>
            </p>
            <p data-agent-uid className="text-[11px] text-[var(--color-text-muted)]">
              Agent id <span className="font-mono">{plan.agent_uid}</span> — the same
              agent on every install that imported this bundle, whatever it is named
              there.
            </p>
            {/* The label is not decoration: a handle rendered next to a
                download without one reads as provenance, and Condor
                verifies nothing here — it signs no bundle and has no keys
                to check one with. */}
            {(plan.author.name || plan.author.handle) && (
              <p data-bundle-author className="text-[11px] text-[var(--color-text-muted)]">
                Author (unverified claim):{" "}
                <span className="text-[var(--color-text)]">
                  {plan.author.name || plan.author.handle}
                </span>
                {plan.author.name && plan.author.handle && ` (${plan.author.handle})`}
              </p>
            )}
          </ReviewSection>

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
                    already have — version {planDisplayVersion} against the one installed.
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

          <div className="flex items-center justify-between gap-2 pt-1">
            <SecondaryButton onClick={cancel}>Cancel</SecondaryButton>
            <PrimaryButton onClick={() => setStep(2)} disabled={busy}>
              Continue
            </PrimaryButton>
          </div>
            </>
          )}

          {/* Step 2 — confirm: the one decision this screen exists for, read
              in the owner's terms rather than the file list again. */}
          {step === 2 && (
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-2">
                <SecondaryButton onClick={() => setStep(1)}>Back</SecondaryButton>
              </div>

              <ConsentDisclaimer plan={plan} />

              <div
                className={
                  stickyAction
                    ? "sticky bottom-0 flex items-center justify-between gap-3 border-t border-[var(--color-border)] bg-[var(--color-surface)] py-3"
                    : "flex items-center justify-between gap-3"
                }
              >
                {/* The explicit consent the owner has to give before Install
                    is enabled. */}
                <label className="flex items-start gap-2 text-[11px] text-[var(--color-text)]">
                  <input
                    type="checkbox"
                    data-import-consent
                    aria-label={consentLabel(plan)}
                    aria-describedby={!blocked && !consented ? "import-consent-hint" : undefined}
                    checked={consented}
                    onChange={(e) => setConsented(e.target.checked)}
                    className="mt-0.5 h-3.5 w-3.5 shrink-0"
                  />
                  {consentLabel(plan)}
                </label>
                <PrimaryButton
                  onClick={confirm}
                  disabled={busy || blocked || !consented}
                  title={!blocked && !consented ? "Tick the box above to enable Import" : undefined}
                  aria-describedby={!blocked && !consented ? "import-consent-hint" : undefined}
                >
                  <Upload className="h-4 w-4" />
                  Import {plan.slug}
                </PrimaryButton>
              </div>
              {!blocked && !consented && (
                <p
                  id="import-consent-hint"
                  className="text-[11px] text-[var(--color-text-muted)]"
                >
                  Tick the box above to enable Import.
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {update && (
        <div
          data-update-plan
          className="space-y-3 rounded-lg border border-[var(--color-primary)]/30 bg-[var(--color-surface)] p-3"
        >
          <div>
            <p className="text-sm font-semibold text-[var(--color-text)]">
              {noChanges ? (
                <>
                  <span className="font-mono">{update.slug}</span> is already up to
                  date at version {updateDisplayVersion}
                </>
              ) : (
                <>
                  Updating <span className="font-mono">{update.slug}</span> — version{" "}
                  {updateDisplayInstalledVersion || "?"} → {updateDisplayVersion}
                </>
              )}
            </p>
            <p className="text-[11px] text-[var(--color-text-muted)]">
              {noChanges ? (
                "Every file here already matches this agent's library. There is nothing to install."
              ) : (
                <>
                  Its memory, config, mutes, journals and session output are untouched.
                  Nothing is started
                  {updateConsentNote(update.hub_reviewed, keptLocalRoutines)}
                </>
              )}
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
                    const copy =
                      f.verdict === "added"
                        ? { label: addedLabel(f.path, update.mutes, update.hub_reviewed) }
                        : VERDICT_COPY[f.verdict];
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

              {noChanges ? (
                onDismiss && (
                  <button
                    onClick={onDismiss}
                    className="rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-sm font-semibold text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                  >
                    Close
                  </button>
                )
              ) : (
                <>
                  {update.hub_reviewed && (
                    // The same explicit tick a fresh hub install requires
                    // before Botcamp's terms acceptance is recorded — an
                    // update records that same acceptance, so it asks the
                    // same way.
                    <label className="flex items-start gap-2 text-[11px] text-[var(--color-text)]">
                      <input
                        type="checkbox"
                        data-update-consent
                        aria-label={updateConsentLabel(update)}
                        checked={consented}
                        onChange={(e) => setConsented(e.target.checked)}
                        className="mt-0.5 h-3.5 w-3.5 shrink-0"
                      />
                      {updateConsentLabel(update)}
                    </label>
                  )}
                  <button
                    onClick={confirmUpdate}
                    disabled={busy || (update.hub_reviewed && !consented)}
                    title={
                      update.hub_reviewed && !consented
                        ? "Tick the box above to enable Update"
                        : undefined
                    }
                    className="flex items-center gap-1.5 rounded-lg border border-[var(--color-primary)]/40 bg-[var(--color-primary)]/10 px-3 py-1.5 text-sm font-semibold text-[var(--color-primary)] transition-all hover:bg-[var(--color-primary)]/20 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <RefreshCw className="h-4 w-4" />
                    Update {update.slug}
                  </button>
                </>
              )}
            </>
          )}
        </div>
      )}
        </div>
      </ScreenFade>
    </div>
  );
}

/**
 * The outcome, once an import or update has landed — a success state of its
 * own rather than the review screen with one more paragraph bolted on.
 *
 * Every fact here is read off the server's own response (the same
 * plan/update shape the review itself rendered), never asserted independently
 * — exactly one of `landed`/`applied` is ever set, by the two confirm
 * handlers above, so there is nothing to reconcile between them.
 */
function SuccessScreen({
  landed,
  applied,
  onOpenAgent,
  onDismiss,
}: {
  landed: AgentImportPlan | null;
  applied: AgentUpdatePlan | null;
  onOpenAgent?: (slug: string) => void;
  onDismiss?: () => void;
}) {
  const result = landed ?? applied;
  if (!result) return null;
  const { slug, hub_reviewed: hubReviewed, findings, mutes } = result;
  const mutedNames = [...mutes.routines, ...mutes.skills];
  const isUpdate = applied !== null;
  // The Botcamp listing number, not the exporting install's own internal
  // counter — see `planDisplayVersion`/`updateDisplayVersion` above
  // (CORR-1019). `landed` and `applied` carry the same two fields.
  const displayVersion = hubReviewed ? result.hub_version : result.version;
  // Same rule as the pre-confirm update screen (`keptLocalRoutines` above):
  // these are the routines the hub's auto-allow never covered, because the
  // bytes it wrote are the operator's own edit, not the hub's reviewed text.
  const keptLocalRoutines = applied
    ? applied.files
        .filter((f) => VERDICT_COPY[f.verdict]?.kept)
        .map((f) => routineNameOf(f.path))
        .filter((name): name is string => name !== null)
    : [];

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 rounded-xl border border-[var(--color-primary)]/30 bg-[var(--color-primary)]/5 p-3.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--color-primary)]/15 text-[var(--color-primary)]">
          <Check className="h-4 w-4" />
        </span>
        <p className="min-w-0 text-sm font-semibold text-[var(--color-text)]">
          {landed ? "Imported" : "Updated"} <span className="font-mono">{slug}</span>
          {applied && <> to version {displayVersion}</>}
        </p>
      </div>

      {/* One paragraph of what actually happened and what to do next, not
          four cards repeating the same reassurance — the three facts worth
          keeping, read as prose instead of scanned as tiles. */}
      <p data-import-facts className="text-sm leading-relaxed text-[var(--color-text-muted)]">
        {landed && <span data-import-done>Nothing is running yet</span>}
        {applied && <span data-update-done>Nothing is running yet</span>} — {isUpdate
          ? "an update"
          : "an import"}{" "}
        only writes files, so nothing in it has executed.{" "}
        {isUpdate
          ? mutedNames.length === 0
            ? "Nothing new muted — every mute it already had stays as it was."
            : `Newly muted: ${mutedNames.join(", ")}. Every mute it already had stays as it was.`
          : mutedNames.length === 0
            ? "Everything it declares — every tool and skill — comes in enabled."
            : `Muted on arrival: ${mutedNames.join(", ")}.`}{" "}
        {isUpdate
          ? // `updateConsentNote` is written as a continuation ("<subject>,
            // and ...") for the pre-confirm screen's "Nothing is started"
            // lead-in. "Nothing is running yet" already opened this
            // paragraph above, so this turns the same clause into its own
            // sentence instead of restating that nothing has run a second
            // time.
            updateConsentNote(hubReviewed, keptLocalRoutines)
              .replace(/^, and /, "")
              .replace(/^./, (c) => c.toUpperCase())
          : hubReviewed
            ? "Every routine arrives with the hub's own review already covering its execution — no further allow is needed."
            : "Its routines still need your allow, one at a time, before any of them runs."}
      </p>

      {/* The same per-file diff the pre-confirm update screen showed, kept
          here rather than dropped once it lands: a kept-local verdict is the
          one the operator most needs after the fact, since the fact above
          already names it only in aggregate. */}
      {applied && (
        <div
          data-update-files
          className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-2.5"
        >
          <p className="mb-1 text-xs font-semibold text-[var(--color-text)]">
            {applied.files.length} file{applied.files.length !== 1 ? "s" : ""}
          </p>
          <ul className="max-h-48 space-y-0.5 overflow-y-auto pr-0.5">
            {[...applied.files]
              .sort(
                (a, b) =>
                  VERDICT_ORDER.indexOf(a.verdict) - VERDICT_ORDER.indexOf(b.verdict) ||
                  a.path.localeCompare(b.path),
              )
              .map((f) => {
                const copy =
                  f.verdict === "added"
                    ? { label: addedLabel(f.path, applied.mutes, applied.hub_reviewed) }
                    : VERDICT_COPY[f.verdict];
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
                    <span className="font-mono text-[var(--color-text)]">{f.path}</span>
                    <span
                      className={
                        copy?.kept ? "text-amber-400" : "text-[var(--color-text-muted)]"
                      }
                    >
                      {copy?.label ?? f.verdict}
                    </span>
                  </li>
                );
              })}
          </ul>
          <p className="mt-1.5 text-[11px] text-[var(--color-text-muted)]">
            The previous bytes of every replaced or deleted file are kept beside the
            agent, out of its library.
          </p>
        </div>
      )}

      {findings.length > 0 && (
        <ReviewSection
          summary={`View the ${findings.length} finding${findings.length !== 1 ? "s" : ""}`}
        >
          <div className="@container">
            <div className="grid grid-cols-1 gap-2 @md:grid-cols-2 @2xl:grid-cols-3">
              {categoriesOf(findings).map((category) => {
                const rows = findings.filter((f) => f.category === category);
                const copy = FINDING_COPY[category];
                return (
                  <div
                    key={category}
                    className="min-w-0 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] p-2"
                  >
                    <p className="text-[11px] font-semibold text-[var(--color-text)]">
                      {copy?.title ?? category}{" "}
                      <span className="font-normal text-[var(--color-text-muted)]">
                        ({rows.length})
                      </span>
                    </p>
                    <ul className="mt-0.5 max-h-40 space-y-0.5 overflow-y-auto pr-0.5">
                      {rows.map((f) => (
                        <li
                          key={`${f.path}:${f.start}:${f.label}`}
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
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })}
            </div>
          </div>
        </ReviewSection>
      )}

      {(onOpenAgent || onDismiss) && (
        <div className="flex items-center gap-2 border-t border-[var(--color-border)] pt-3">
          {onOpenAgent && (
            <PrimaryButton onClick={() => onOpenAgent(slug)}>Start chatting</PrimaryButton>
          )}
          {onDismiss && <SecondaryButton onClick={onDismiss}>Close</SecondaryButton>}
        </div>
      )}
    </div>
  );
}
