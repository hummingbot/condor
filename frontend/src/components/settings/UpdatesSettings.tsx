import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowUpCircle,
  CheckCircle2,
  Circle,
  Loader2,
  MinusCircle,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import {
  type Block,
  type ComponentStatus,
  type Preflight,
  type Run,
  type Step,
  UPDATES_RUN_KEY,
  UPDATES_STATUS_KEY,
  isLive,
  updatesApi,
} from "@/lib/updates-api";

/**
 * Everything `/update` can do in Telegram, for an admin in the browser (FEAT-071).
 *
 * A view over `condor/updates` — the same engine Telegram talks to, so a run
 * started there shows up here and vice versa. Nothing about *how* to update
 * lives in this file.
 *
 * The panel has four states, and the interesting one is the third: starting a
 * Condor update kills the server rendering this page. That is not an error path
 * to survive, it is the normal middle of the happy path. The engine writes
 * `state: "restarting"` to the journal *before* the process exits, so a failed
 * fetch that follows that state means "restarting", not "the backend died" —
 * we keep polling through the gap and the first answer that comes back carries
 * a run the boot hook has already judged.
 */

/** How long to wait on a restart before the copy stops being reassuring. */
const RESTART_PATIENCE_MS = 5 * 60 * 1000;

export function UpdatesSettings() {
  const qc = useQueryClient();

  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState<Block | null>(null);
  const [dismissedRunId, setDismissedRunId] = useState<string | null>(null);

  const status = useQuery({
    queryKey: UPDATES_STATUS_KEY,
    queryFn: updatesApi.getStatus,
    retry: false,
  });

  // Polled while a run is live. `retry` is pinned rather than left at the
  // default: during a Condor update a failed fetch is the expected signal, and
  // a query that gave up would strand the panel exactly when it matters.
  const runQuery = useQuery({
    queryKey: UPDATES_RUN_KEY,
    queryFn: updatesApi.getRun,
    retry: true,
    refetchInterval: (query) => (isLive(query.state.data?.run) ? 2000 : false),
  });

  // On error TanStack keeps the last successful data, which is what lets the
  // restart gap render from the run we saw just before the process exited.
  const run = runQuery.data?.run ?? null;
  const active = run && run.id !== dismissedRunId ? run : null;

  const checkMut = useMutation({
    mutationFn: updatesApi.check,
    onSuccess: (data) => qc.setQueryData(UPDATES_STATUS_KEY, data),
  });

  const preflightMut = useMutation({
    mutationFn: (components: string[]) => updatesApi.preflight(components),
    onSuccess: setPreflight,
  });

  const resolveMut = useMutation({
    mutationFn: ({ component, action }: { component: string; action: string }) =>
      updatesApi.resolve(component, action),
    onSuccess: (result) => {
      setConfirmDiscard(null);
      // Re-run the preflight rather than patching the blocker out of local
      // state: resolving one conflict can reveal or clear others, and the
      // engine is the only thing that knows.
      if (result.ok && preflight) preflightMut.mutate(preflight.components);
    },
  });

  const startMut = useMutation({
    mutationFn: (components: string[]) => updatesApi.start(components),
    onSuccess: () => {
      setPreflight(null);
      setDismissedRunId(null);
      qc.invalidateQueries({ queryKey: UPDATES_RUN_KEY });
    },
  });

  // A run that just finished leaves the status stale — the whole point is that
  // the versions moved.
  const finishedId = !isLive(run) ? (run?.id ?? null) : null;
  useEffect(() => {
    if (finishedId) qc.invalidateQueries({ queryKey: UPDATES_STATUS_KEY });
  }, [finishedId, qc]);

  if (status.isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-[var(--color-text-muted)]">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  if (active && isLive(active)) {
    return <RunningView run={active} offline={runQuery.isError} />;
  }

  if (active) {
    return (
      <FinishedView
        run={active}
        onDismiss={() => {
          setDismissedRunId(active.id);
          status.refetch();
        }}
      />
    );
  }

  if (preflight) {
    return (
      <>
        <PreflightView
          preflight={preflight}
          isStarting={startMut.isPending}
          isResolving={resolveMut.isPending}
          error={
            (startMut.error as Error | null)?.message ??
            (resolveMut.data && !resolveMut.data.ok ? resolveMut.data.message : null)
          }
          onResolve={(block, action) =>
            action === "discard"
              ? setConfirmDiscard(block)
              : resolveMut.mutate({ component: block.component, action })
          }
          onStart={() => startMut.mutate(preflight.components)}
          onCancel={() => setPreflight(null)}
        />
        <ConfirmDialog
          open={confirmDiscard !== null}
          title="Discard local changes?"
          confirmLabel="Discard"
          pendingLabel="Discarding..."
          isPending={resolveMut.isPending}
          isError={resolveMut.isError}
          errorText={(resolveMut.error as Error | null)?.message}
          onConfirm={() =>
            confirmDiscard &&
            resolveMut.mutate({
              component: confirmDiscard.component,
              action: "discard",
            })
          }
          onClose={() => setConfirmDiscard(null)}
        >
          <p>
            These files will be reset to the last commit. The changes cannot be
            recovered — <strong>Stash</strong> keeps them instead.
          </p>
          <ul className="mt-3 max-h-40 space-y-0.5 overflow-y-auto font-mono text-xs">
            {confirmDiscard?.paths.map((p) => <li key={p}>{p}</li>)}
          </ul>
        </ConfirmDialog>
      </>
    );
  }

  const components = status.data?.components ?? [];
  const stale = components.filter((c) => !c.up_to_date);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs leading-relaxed text-[var(--color-text-muted)]">
          Updating restarts the component. Condor restarting takes this page with
          it — it reconnects on its own.
        </p>
        <button
          onClick={() => checkMut.mutate()}
          disabled={checkMut.isPending}
          className="flex shrink-0 items-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:opacity-50"
        >
          <RefreshCw
            className={`h-3.5 w-3.5 ${checkMut.isPending ? "animate-spin" : ""}`}
          />
          Refresh
        </button>
      </div>

      {status.isError && (
        <p className="text-xs text-[var(--color-red)]">
          {(status.error as Error).message}
        </p>
      )}

      {components.map((c) => (
        <ComponentCard
          key={c.key}
          component={c}
          isPending={preflightMut.isPending}
          onUpdate={() => preflightMut.mutate([c.key])}
        />
      ))}

      {stale.length > 1 && (
        <button
          onClick={() => preflightMut.mutate(stale.map((c) => c.key))}
          disabled={preflightMut.isPending}
          className="w-full rounded-lg bg-[var(--color-primary)]/15 px-3 py-2 text-sm font-medium text-[var(--color-primary)] transition-opacity hover:opacity-80 disabled:opacity-50"
        >
          Update all ({stale.length})
        </button>
      )}

      {preflightMut.isError && (
        <p className="text-xs text-[var(--color-red)]">
          {(preflightMut.error as Error).message}
        </p>
      )}
    </div>
  );
}

/** One component's versions. Two facets when a thing is versioned two ways. */
function ComponentCard({
  component,
  isPending,
  onUpdate,
}: {
  component: ComponentStatus;
  isPending: boolean;
  onUpdate: () => void;
}) {
  const facets = Object.entries(component.facets);

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text)]">
            {component.name}
            {component.mode && (
              <span className="rounded bg-[var(--color-surface-hover)] px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
                {component.mode}
              </span>
            )}
          </h3>
        </div>
        {component.up_to_date ? (
          <span className="flex shrink-0 items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
            <CheckCircle2 className="h-3.5 w-3.5" />
            Up to date
          </span>
        ) : (
          <button
            onClick={onUpdate}
            disabled={isPending}
            className="flex shrink-0 items-center gap-1.5 rounded-md bg-[var(--color-primary)]/15 px-3 py-1.5 text-xs font-medium text-[var(--color-primary)] transition-opacity hover:opacity-80 disabled:opacity-50"
          >
            <ArrowUpCircle className="h-3.5 w-3.5" />
            Update
          </button>
        )}
      </div>

      <div className="mt-3 space-y-2">
        {facets.map(([kind, facet]) =>
          !facet ? null : (
            <div key={kind} className="text-xs">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <span className="w-12 shrink-0 uppercase tracking-wide text-[var(--color-text-muted)]">
                  {kind}
                </span>
                <span className="font-mono text-[var(--color-text)]">
                  {facet.current}
                </span>
                {facet.available && !facet.up_to_date && (
                  <>
                    <span className="text-[var(--color-text-muted)]">→</span>
                    <span className="font-mono text-[var(--color-primary)]">
                      {facet.available}
                    </span>
                  </>
                )}
                {facet.behind > 0 && (
                  <span className="text-[var(--color-text-muted)]">
                    {facet.behind} commit{facet.behind === 1 ? "" : "s"} behind
                  </span>
                )}
              </div>
              {facet.error && (
                <p className="mt-1 pl-14 text-[var(--color-red)]">{facet.error}</p>
              )}
              {facet.detail.length > 0 && (
                <details className="mt-1 pl-14">
                  <summary className="cursor-pointer text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
                    What&apos;s new
                  </summary>
                  <ul className="mt-1 space-y-0.5 text-[var(--color-text-muted)]">
                    {facet.detail.map((line, i) => (
                      <li key={i} className="truncate font-mono">
                        {line}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          ),
        )}
      </div>
    </div>
  );
}

/** The plan, what it will cost, and anything standing in the way. */
function PreflightView({
  preflight,
  isStarting,
  isResolving,
  error,
  onResolve,
  onStart,
  onCancel,
}: {
  preflight: Preflight;
  isStarting: boolean;
  isResolving: boolean;
  error: string | null;
  onResolve: (block: Block, action: string) => void;
  onStart: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <h3 className="text-sm font-semibold text-[var(--color-text)]">
          This will run
        </h3>
        <ol className="mt-2 space-y-1 text-xs text-[var(--color-text-muted)]">
          {preflight.steps.map((step, i) => (
            <li key={i} className="flex gap-2">
              <span className="w-4 shrink-0 text-right tabular-nums">{i + 1}.</span>
              {step}
            </li>
          ))}
        </ol>
      </div>

      {preflight.warnings.map((w) => (
        <div
          key={`${w.component}:${w.code}`}
          className="flex items-start gap-2 rounded-lg border border-[var(--color-yellow)]/40 bg-[var(--color-yellow)]/10 p-3 text-xs text-[var(--color-text)]"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-yellow)]" />
          <span>{w.message}</span>
        </div>
      ))}

      {preflight.blocks.map((block) => (
        <div
          key={`${block.component}:${block.code}`}
          className="rounded-lg border border-[var(--color-red)]/40 bg-[var(--color-red)]/5 p-4"
        >
          <h4 className="flex items-center gap-1.5 text-sm font-semibold text-[var(--color-text)]">
            <XCircle className="h-3.5 w-3.5 text-[var(--color-red)]" />
            {block.message}
          </h4>
          {block.paths.length > 0 && (
            <ul className="mt-2 max-h-40 space-y-0.5 overflow-y-auto font-mono text-xs text-[var(--color-text-muted)]">
              {block.paths.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
          {block.resolutions.length > 0 && (
            <div className="mt-3 flex gap-2">
              {block.resolutions.map((action) => (
                <button
                  key={action}
                  onClick={() => onResolve(block, action)}
                  disabled={isResolving}
                  className={`rounded-md px-3 py-1.5 text-xs font-medium capitalize transition-colors disabled:opacity-50 ${
                    action === "discard"
                      ? "bg-[var(--color-red)]/15 text-[var(--color-red)] hover:opacity-80"
                      : "border border-[var(--color-border)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                  }`}
                >
                  {action}
                </button>
              ))}
            </div>
          )}
        </div>
      ))}

      {error && <p className="text-xs text-[var(--color-red)]">{error}</p>}

      <div className="flex gap-2">
        <button
          onClick={onCancel}
          className="rounded-md px-3 py-2 text-sm text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
        >
          Cancel
        </button>
        <button
          onClick={onStart}
          disabled={!preflight.ok || isStarting}
          title={preflight.ok ? undefined : "Resolve the blockers first"}
          className="flex-1 rounded-md bg-[var(--color-primary)]/15 px-3 py-2 text-sm font-medium text-[var(--color-primary)] transition-opacity hover:opacity-80 disabled:opacity-40"
        >
          {isStarting ? "Starting..." : "Start update"}
        </button>
      </div>
    </div>
  );
}

/** Step by step, including the stretch where there is no server to ask. */
function RunningView({ run, offline }: { run: Run; offline: boolean }) {
  const restarting = run.state === "restarting";

  // When patience runs out the copy stops promising a reconnect. The panel
  // keeps polling regardless — if Condor does come back, this heals itself.
  const since = useRef<number | null>(null);
  const [waitedTooLong, setWaitedTooLong] = useState(false);
  useEffect(() => {
    if (!restarting) {
      since.current = null;
      setWaitedTooLong(false);
      return;
    }
    since.current ??= Date.now();
    const timer = setInterval(() => {
      if (since.current && Date.now() - since.current > RESTART_PATIENCE_MS) {
        setWaitedTooLong(true);
      }
    }, 5000);
    return () => clearInterval(timer);
  }, [restarting]);

  return (
    <div className="space-y-4">
      {/* A failed fetch after `restarting` is the expected signal, not an error,
          so both render the same reassurance. */}
      {(restarting || offline) && (
        <div className="flex items-start gap-2 rounded-lg border border-[var(--color-primary)]/40 bg-[var(--color-primary)]/10 p-3 text-xs text-[var(--color-text)]">
          <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-[var(--color-primary)]" />
          <span>
            {waitedTooLong
              ? "Condor has not come back. Check the terminal — this page will keep trying."
              : "Condor is restarting — this page will reconnect on its own."}
          </span>
        </div>
      )}

      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <h3 className="text-sm font-semibold text-[var(--color-text)]">
          Updating {run.components.join(", ")}
        </h3>
        <ul className="mt-3 space-y-2">
          {run.steps.map((step) => (
            <StepRow key={step.key} step={step} />
          ))}
        </ul>
      </div>
    </div>
  );
}

function StepRow({ step }: { step: Step }) {
  return (
    <li className="text-xs">
      <div className="flex items-center gap-2">
        <StepIcon state={step.state} />
        <span
          className={
            step.state === "pending"
              ? "text-[var(--color-text-muted)]"
              : "text-[var(--color-text)]"
          }
        >
          {step.label}
        </span>
      </div>
      {step.state === "failed" && step.output_tail && (
        <details className="mt-1 pl-6">
          <summary className="cursor-pointer text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
            Output
          </summary>
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded bg-[var(--color-bg)] p-2 font-mono text-[11px] text-[var(--color-text-muted)]">
            {step.output_tail}
          </pre>
        </details>
      )}
    </li>
  );
}

function StepIcon({ state }: { state: Step["state"] }) {
  const cls = "h-3.5 w-3.5 shrink-0";
  if (state === "running")
    return <Loader2 className={`${cls} animate-spin text-[var(--color-primary)]`} />;
  if (state === "ok")
    return <CheckCircle2 className={`${cls} text-[var(--color-green)]`} />;
  if (state === "failed") return <XCircle className={`${cls} text-[var(--color-red)]`} />;
  if (state === "skipped")
    return <MinusCircle className={`${cls} text-[var(--color-text-muted)]`} />;
  return <Circle className={`${cls} text-[var(--color-text-muted)]`} />;
}

/** How it ended — including a run this process only learned about at boot. */
function FinishedView({ run, onDismiss }: { run: Run; onDismiss: () => void }) {
  const ok = run.state === "succeeded";

  return (
    <div className="space-y-4">
      <div
        className={`rounded-lg border p-4 ${
          ok
            ? "border-[var(--color-green)]/40 bg-[var(--color-green)]/10"
            : "border-[var(--color-red)]/40 bg-[var(--color-red)]/5"
        }`}
      >
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-[var(--color-text)]">
          {ok ? (
            <CheckCircle2 className="h-3.5 w-3.5 text-[var(--color-green)]" />
          ) : (
            <XCircle className="h-3.5 w-3.5 text-[var(--color-red)]" />
          )}
          {ok
            ? `Updated ${run.components.join(", ")}`
            : `Update of ${run.components.join(", ")} failed`}
        </h3>
        {run.error && (
          <p className="mt-1.5 text-xs text-[var(--color-text-muted)]">{run.error}</p>
        )}
      </div>

      <ul className="space-y-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        {run.steps.map((step) => (
          <StepRow key={step.key} step={step} />
        ))}
      </ul>

      <button
        onClick={onDismiss}
        className="w-full rounded-md border border-[var(--color-border)] px-3 py-2 text-sm font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
      >
        Done
      </button>
    </div>
  );
}
