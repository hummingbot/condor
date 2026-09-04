import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  BookOpen,
  FlaskConical,
  GraduationCap,
  Pencil,
  Power,
  ScrollText,
  Sliders,
  Trash2,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { MarkdownEditor } from "@/components/agent/AgentOverviewTab";
import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import { DiscardChangesDialog } from "@/components/editor/EditorDialogs";
import { ReportBrowser } from "@/components/routines/ReportBrowser";
import { countdown } from "@/lib/agent-attribution";
import { api, type StrategyDetail } from "@/lib/api";
import { formatCurrency } from "@/lib/formatters";

/**
 * The Playbook disclosure's body: what the strategy is *told*, not what it did.
 *
 * This band used to host the whole `StrategyWorkbench` — which is a page, and
 * was written to be one. On the chat's pane it still is the right thing, and it
 * is unchanged there. On this screen it was the same page a second time: its
 * `<h1>` repeated the header two inches above, its `AgentControls` repeated the
 * header's Pause/Stop, its `LoopPulse` repeated the loop bar's cadence and
 * countdown, its `DeployedFleet` repeated the answer stack's ledger — under a
 * *different* count, since the two fold differently — and its `PerformancePanel`
 * repeated the vitals strip, which is the very duplication `MoneyView` removed
 * from the Money band for the same reason. Meanwhile the one thing the band is
 * named for, `strategy.md`, was not on screen at all: it was behind a button,
 * in a modal, over a page that was already a copy of the page behind it.
 *
 * So this is the band cut to its own promise — *the strategy's playbook, its
 * config and what it has learned* — and to the two doors that exist nowhere
 * else on the screen (its reports, and deleting it).
 *
 * Read first, edit on request. The playbook and the learnings are markdown a
 * model wrote and a person mostly reads; a textarea as the resting state hands
 * the reader raw `##` and asterisks and asks them to parse it. `MarkdownEditor`
 * is still what edits them, mounted only while editing — which also means it
 * re-reads the saved file every time it opens, rather than holding a stale
 * buffer across a save.
 */
export function PlaybookView({
  slug,
  sslug,
  strategy,
  onDeleted,
}: {
  slug: string;
  sslug: string;
  /** The strategy the screen already resolved — this band buys no second poll. */
  strategy: StrategyDetail;
  /** The host's move after a delete: the run screen drops `?strategy=`. */
  onDeleted: () => void;
}) {
  const queryClient = useQueryClient();
  const [showRoutines, setShowRoutines] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteStrategy(slug, sslug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent", slug] });
      queryClient.invalidateQueries({ queryKey: ["agent-brain", slug] });
      onDeleted();
    },
  });

  const { data: routineInstances = [] } = useQuery({
    queryKey: ["routine-instances"],
    queryFn: api.getRoutineInstances,
    enabled: showRoutines,
    refetchInterval: 5000,
  });

  const dryRuns = strategy.experiments.length;
  const newestDryRun = dryRuns
    ? Math.max(...strategy.experiments.map((e) => e.number))
    : 0;

  return (
    <div className="space-y-4 pt-3">
      {/* ① What it is for, and the two doors that are only here. The name is
          deliberately absent: the loop bar names the strategy in scope and the
          header names the agent, and a third copy is the duplication this band
          exists to have stopped. */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          {strategy.description ? (
            <p className="max-w-3xl text-sm leading-relaxed text-[var(--color-text)]">
              {strategy.description}
            </p>
          ) : (
            <p className="text-sm italic text-[var(--color-text-muted)]">
              This strategy has no description.
            </p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-[var(--color-text-muted)]">
            <Chip>
              {strategy.sessions.length} session
              {strategy.sessions.length === 1 ? "" : "s"}
            </Chip>
            {/* A strategy whose whole history is one dry run used to read as
                one that had never run, so the count is a link and not a note. */}
            {dryRuns > 0 && (
              <Link
                to={`/agents/${encodeURIComponent(slug)}?open=runs&strategy=${encodeURIComponent(
                  sslug,
                )}&run=e${newestDryRun}`}
                className="flex items-center gap-1 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-amber-500 transition-colors hover:bg-amber-500/20"
              >
                <FlaskConical className="h-3 w-3" />
                {dryRuns} dry run{dryRuns === 1 ? "" : "s"}
              </Link>
            )}
            <Chip mono>{strategy.slug}</Chip>
            {strategy.agent_id && <Chip mono>{strategy.agent_id}</Chip>}
          </div>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setShowRoutines(true)}
            className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
          >
            <ScrollText className="h-3.5 w-3.5" /> Routines &amp; reports
          </button>
          <button
            type="button"
            onClick={() => setShowDeleteConfirm(true)}
            disabled={strategy.status === "running"}
            title={
              strategy.status === "running"
                ? "Stop the strategy before deleting it"
                : "Delete this strategy"
            }
            className="flex items-center gap-1.5 rounded-lg border border-red-500/30 bg-red-500/10 px-2.5 py-1.5 text-xs font-semibold text-red-400 transition-all hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-30"
          >
            <Trash2 className="h-3.5 w-3.5" /> Delete
          </button>
        </div>
      </div>

      {/* ② The two documents, side by side at width. They are the same kind of
          thing read two ways — what a session is told, and what sessions have
          written back — so they are one row rather than one above the other. */}
      <div className="grid items-start gap-4 lg:grid-cols-2">
        <DocCard
          title="Playbook"
          file="strategy.md"
          Icon={BookOpen}
          content={strategy.strategy_md}
          empty="Nothing yet. The playbook is the standing brief every session of this strategy is given before its first tick — what it trades, what it may not do, and how to decide."
          onSave={(value) => api.updateStrategyMd(slug, sslug, value)}
          invalidateKey={["strategy", slug, sslug]}
        />
        <DocCard
          title="Learnings"
          file="persists across sessions"
          Icon={GraduationCap}
          content={strategy.learnings}
          empty="Nothing yet. Sessions append what they worked out here, and every later session reads it back."
          onSave={(value) => api.updateStrategyLearnings(slug, sslug, value)}
          invalidateKey={["strategy", slug, sslug]}
        />
      </div>

      {/* ③ The configuration the loop runs under. It was readable only inside a
          running session's instance card, which means a stopped strategy could
          not be inspected at all — and it was printed raw, where `-1` is a
          disabled limit and reads as a number. */}
      <ConfigCard slug={slug} sslug={sslug} strategy={strategy} />

      {showRoutines && (
        <ReportBrowser
          initialSourceTypeFilter={slug}
          instances={routineInstances}
          onClose={() => setShowRoutines(false)}
        />
      )}

      <ConfirmDialog
        open={showDeleteConfirm}
        title="Delete Strategy"
        isPending={deleteMutation.isPending}
        isError={deleteMutation.isError}
        errorText="Failed to delete strategy. It may be running."
        onConfirm={() => deleteMutation.mutate()}
        onClose={() => setShowDeleteConfirm(false)}
      >
        Delete <strong className="text-[var(--color-text)]">{strategy.name}</strong>?
        This cannot be undone.
      </ConfirmDialog>
    </div>
  );
}

/** One identity fact, said the way every other chip on this screen is. */
function Chip({
  children,
  mono = false,
}: {
  children: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <span
      className={`rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-0.5 ${
        mono ? "font-mono" : ""
      }`}
    >
      {children}
    </span>
  );
}

/**
 * One markdown file: rendered, until somebody asks to edit it.
 *
 * The body is height-capped and scrolls inside itself. A learnings file grows
 * without bound — every session appends to it — and letting it set the height
 * of the band would push the disclosure's own footer past the end of a long
 * page, which is the failure the height cap on the Runs and Fleet bands already
 * answers one screen up.
 */
function DocCard({
  title,
  file,
  Icon,
  content,
  empty,
  onSave,
  invalidateKey,
}: {
  title: string;
  /** Where it lives, said in the header rather than assumed. */
  file: string;
  Icon: typeof BookOpen;
  content: string;
  /** What this file is for, shown when there is none — not "no content". */
  empty: string;
  onSave: (value: string) => Promise<unknown>;
  invalidateKey: unknown[];
}) {
  const [editing, setEditing] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);

  const close = () => {
    setEditing(false);
    setDirty(false);
    setConfirmDiscard(false);
  };

  return (
    <section className="flex min-w-0 flex-col rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <header className="flex items-center gap-2 border-b border-[var(--color-border)] px-3 py-2">
        <Icon className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
        <h3 className="text-xs font-bold uppercase tracking-widest text-[var(--color-text)]">
          {title}
        </h3>
        <span className="min-w-0 truncate font-mono text-[10px] text-[var(--color-text-muted)]">
          {file}
        </span>
        <button
          type="button"
          onClick={() => (editing ? (dirty ? setConfirmDiscard(true) : close()) : setEditing(true))}
          className="ml-auto flex shrink-0 items-center gap-1 rounded-md border border-[var(--color-border)] px-2 py-1 text-[11px] font-semibold text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
        >
          {editing ? (
            <>
              <X className="h-3 w-3" /> Done
            </>
          ) : (
            <>
              <Pencil className="h-3 w-3" /> Edit
            </>
          )}
        </button>
      </header>

      <div className="min-w-0 p-3">
        {editing ? (
          <MarkdownEditor
            label={title}
            sublabel={file}
            content={content}
            onSave={onSave}
            invalidateKey={invalidateKey}
            onDirtyChange={setDirty}
            minHeightClass="min-h-[340px]"
            showLabel={false}
          />
        ) : content.trim() ? (
          <div className="chat-markdown max-h-[420px] overflow-y-auto text-sm leading-relaxed text-[var(--color-text)]">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
          </div>
        ) : (
          <div className="flex items-start gap-2 rounded-md border border-dashed border-[var(--color-border)] p-3 text-xs leading-relaxed text-[var(--color-text-muted)]">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{empty}</span>
          </div>
        )}
      </div>

      {confirmDiscard && (
        <DiscardChangesDialog
          fileName={file}
          onDiscard={close}
          onClose={() => setConfirmDiscard(false)}
        />
      )}
    </section>
  );
}

// ── The configuration, read as settings rather than as a payload ──

/** One setting. `off` is a limit that is switched off, drawn as such. */
type Row = { label: string; value: string; off?: boolean; hint?: string };

/** `3600` → `1h 00m`; `0` is not a cadence, it is the absence of one. */
function seconds(v: unknown, none = "none"): string {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return none;
  return countdown(n);
}

/** A limit where a negative is "no limit", which is how the engine reads it. */
function limit(v: unknown, fmt: (n: number) => string): Row["value"] | null {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0) return null;
  return fmt(n);
}

function text(v: unknown, fallback: string): string {
  const s = typeof v === "string" ? v.trim() : v === undefined || v === null ? "" : String(v);
  return s || fallback;
}

/** Every key the groups below spell out, so the leftovers can be found. */
const NAMED = new Set([
  "execution_mode",
  "frequency_sec",
  "tick_timeout_sec",
  "max_ticks",
  "restart_on_boot",
  "server_name",
  "bot_mode",
  "bot_name",
  "agent_key",
  "model_base_url",
  "total_amount_quote",
  "risk_limits",
  "canvas_enabled",
  "canvas_nudge_ticks",
  "canvas_band_usd",
  "trading_context",
]);

function ConfigCard({
  slug,
  sslug,
  strategy,
}: {
  slug: string;
  sslug: string;
  strategy: StrategyDetail;
}) {
  const queryClient = useQueryClient();
  const config = strategy.config || {};
  const risk = (config.risk_limits || {}) as Record<string, unknown>;

  /**
   * "Resume after restart", which used to live on `LoopPulse`'s spine.
   *
   * Taking the pulse off this screen would have taken the toggle with it, and
   * this is a better home for it than a header ornament was: it is not a fact
   * about the current tick, it is a setting the loop runs under. Written
   * straight through and invalidated, so the switch reflects what is on disk
   * and a failed write snaps back instead of leaving the reader believing a
   * loop is armed when it is not.
   */
  const restartMutation = useMutation({
    mutationFn: (enabled: boolean) => api.setRestartOnBoot(slug, sslug, enabled),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["strategy", slug, sslug] });
    },
  });
  const resumes = config.restart_on_boot === true;

  const groups = useMemo(() => {
    const loop: Row[] = [
      { label: "mode", value: text(config.execution_mode, "loop") },
      { label: "cadence", value: seconds(config.frequency_sec, "on demand") },
      {
        label: "tick timeout",
        value: seconds(config.tick_timeout_sec, "none"),
        off: !Number(config.tick_timeout_sec),
      },
      {
        label: "max ticks",
        value: limit(config.max_ticks, (n) => (n > 0 ? String(n) : "unlimited")) ?? "unlimited",
        off: !Number(config.max_ticks),
      },
    ];

    const where: Row[] = [
      { label: "server", value: text(config.server_name, "ambient") },
      { label: "bot mode", value: text(config.bot_mode, "auto") },
      { label: "bot name", value: text(config.bot_name, "—"), off: !config.bot_name },
      {
        label: "model",
        value: text(config.agent_key, "agent default"),
        off: !config.agent_key,
      },
    ];
    if (config.model_base_url) {
      where.push({ label: "base url", value: String(config.model_base_url) });
    }

    const money: Row[] = [
      {
        label: "budget",
        value: formatCurrency(Number(config.total_amount_quote) || 0),
        hint: "What one session may put to work",
      },
      {
        label: "max position",
        value: limit(risk.max_position_size_quote, (n) => formatCurrency(n)) ?? "no limit",
        off: limit(risk.max_position_size_quote, () => "") === null,
      },
      {
        label: "max executors",
        value: limit(risk.max_open_executors, (n) => String(n)) ?? "no limit",
        off: limit(risk.max_open_executors, () => "") === null,
      },
      {
        label: "max drawdown",
        value: limit(risk.max_drawdown_pct, (n) => `${n}%`) ?? "no limit",
        off: limit(risk.max_drawdown_pct, () => "") === null,
      },
      {
        label: "shutdown drawdown",
        value: limit(risk.shutdown_drawdown_pct, (n) => `${n}%`) ?? "no limit",
        off: limit(risk.shutdown_drawdown_pct, () => "") === null,
      },
      {
        label: "max drift",
        value: limit(risk.max_drift_quote, (n) => formatCurrency(n)) ?? "no limit",
        off: limit(risk.max_drift_quote, () => "") === null,
      },
      {
        label: "max leverage",
        value: limit(risk.max_leverage, (n) => `${n}×`) ?? "no limit",
        off: limit(risk.max_leverage, () => "") === null,
      },
    ];

    // Whatever a newer build writes that this dashboard has no name for. Shown
    // rather than dropped: a config the reader cannot see is a config they
    // cannot debug, and the raw key is a worse label than no label is a lie.
    const other: Row[] = Object.entries(config)
      .filter(([k, v]) => !NAMED.has(k) && v !== null && typeof v !== "object")
      .map(([k, v]) => ({ label: k.replace(/_/g, " "), value: String(v) }));

    const canvas: Row[] =
      config.canvas_enabled === undefined
        ? []
        : [
            { label: "canvas", value: config.canvas_enabled ? "on" : "off", off: !config.canvas_enabled },
            { label: "nudge every", value: `${Number(config.canvas_nudge_ticks) || 0} ticks` },
            { label: "band", value: formatCurrency(Number(config.canvas_band_usd) || 0) },
          ];

    return [
      { title: "Loop", rows: loop },
      { title: "Where it runs", rows: where },
      { title: "Money & risk", rows: money },
      ...(canvas.length ? [{ title: "Canvas", rows: canvas }] : []),
      ...(other.length ? [{ title: "Other", rows: other }] : []),
    ];
  }, [config, risk]);

  const context = text(
    config.trading_context || strategy.default_trading_context,
    "",
  );

  return (
    <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <header className="flex flex-wrap items-center gap-2 border-b border-[var(--color-border)] px-3 py-2">
        <Sliders className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
        <h3 className="text-xs font-bold uppercase tracking-widest text-[var(--color-text)]">
          Configuration
        </h3>
        <span className="text-[10px] text-[var(--color-text-muted)]">
          what every session of this strategy runs under
        </span>
        <button
          type="button"
          role="switch"
          aria-checked={resumes}
          disabled={restartMutation.isPending}
          onClick={() => restartMutation.mutate(!resumes)}
          title={
            resumes
              ? "Condor restarts this loop in a fresh session after it restarts. Click to turn off."
              : "This loop stays stopped after Condor restarts. Click to have it resume."
          }
          className={`ml-auto flex shrink-0 items-center gap-1 rounded border px-2 py-1 text-[11px] font-medium transition-colors hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)] disabled:opacity-50 ${
            resumes
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-500"
              : "border-[var(--color-border)] text-[var(--color-text-muted)]"
          }`}
        >
          <Power className="h-3 w-3" />
          {restartMutation.isPending
            ? "saving…"
            : resumes
              ? "resumes on restart"
              : "stops on restart"}
        </button>
      </header>

      <div className="grid gap-x-8 gap-y-5 p-3 sm:grid-cols-2 xl:grid-cols-4">
        {groups.map((group) => (
          <div key={group.title} className="min-w-0">
            <h4 className="mb-1.5 text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
              {group.title}
            </h4>
            <dl className="space-y-1">
              {group.rows.map((row) => (
                <div
                  key={row.label}
                  className="flex items-baseline justify-between gap-3 font-mono text-xs"
                  title={row.hint}
                >
                  <dt className="min-w-0 truncate text-[var(--color-text-muted)]">
                    {row.label}
                  </dt>
                  <dd
                    className={`shrink-0 ${
                      row.off
                        ? "text-[var(--color-text-muted)] opacity-60"
                        : "text-[var(--color-text)]"
                    }`}
                  >
                    {row.value}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>

      {context && (
        <div className="border-t border-[var(--color-border)] px-3 py-2">
          <h4 className="mb-1 text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            Trading context
          </h4>
          <p className="whitespace-pre-wrap text-xs leading-relaxed text-[var(--color-text-muted)]">
            {context}
          </p>
        </div>
      )}
    </section>
  );
}
