import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowLeft, ExternalLink, ScrollText } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { AgentKnowledge } from "@/components/agent/AgentKnowledge";
import { PerformancePanel } from "@/components/agent/AgentOverviewTab";
import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import { StrategyWorkbench } from "@/components/agent/StrategyWorkbench";
import { isKnowledgeTab } from "@/components/agent/knowledgeTabs";
import { WorkspaceHeader } from "@/components/agent/workspace/WorkspaceHeader";
import { WorkspaceSpine } from "@/components/agent/workspace/WorkspaceSpine";
import {
  parseWorkspace,
  pickRun,
  pickStrategy,
  spineSectionFor,
  type WorkspaceViewId,
} from "@/components/agent/workspace/views";
import { ReportBrowser } from "@/components/routines/ReportBrowser";
import { api } from "@/lib/api";

/**
 * One agent, one screen, one route (FEAT-103).
 *
 * The path from a conversation to "Brigado deployed six controllers and they
 * are up $64" used to be six navigations across four pages — the agent page
 * (which opened on an `AGENT.md` dump), the strategy page, the Lab and the chat
 * — and the way back did not exist: the nav's Agents entry goes to the chat, so
 * "Back to Agents" was a link to a conversation and the Lab had no back control
 * at all.
 *
 * So there is one route now, and every state it can be in is a query parameter:
 * `?view=` names the section, `?strategy=` the scope, `?run=` and `?tick=` the
 * moment. That grammar is not invented here — the Lab already put
 * `?strategy=&run=&tick=` in the URL (FEAT-099) and defended it in its own
 * docstring; this generalises it upward over the agent so that a tick stays a
 * *selection* rather than becoming a destination you navigate to.
 *
 * The bodies are all imported unchanged. Four pages were four *shells* around
 * components that were already host-agnostic — `StrategyWorkbench` was hosted
 * by a page and by the chat, `AgentKnowledge` by a page and by the chat, and
 * the Lab's bands are exports of `AgentSessionContent` — so this replaces the
 * shells and keeps every body.
 *
 * Everything the URL says is read by `workspace/views.ts`, never in here: this
 * page reads more parameters than any page before it, and the containment is
 * that none of the rules for reading them live in JSX.
 */
export function AgentWorkspace() {
  const { slug = "" } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [showRoutinesBrowser, setShowRoutinesBrowser] = useState(false);

  const url = useMemo(() => parseWorkspace(searchParams), [searchParams]);

  /**
   * Write a selection into the URL.
   *
   * `replace` for a section change — reading down the sections is not nine
   * steps of history to press Back through — and a real push for a scope, a run
   * or a tick, which is what makes Back move one level shallower from any depth
   * without ever leaving the agent.
   */
  const setParams = useCallback(
    (
      next: Record<string, string | number | null>,
      options?: { replace?: boolean },
    ) => {
      const params = new URLSearchParams(searchParams);
      for (const [key, value] of Object.entries(next)) {
        if (value === null || value === "") params.delete(key);
        else params.set(key, String(value));
      }
      // `view=now` is the default, so it is never spelled out: the shortest URL
      // that lands somewhere is the one people paste.
      if (params.get("view") === "now") params.delete("view");
      params.delete("tab");
      setSearchParams(params, options);
    },
    [searchParams, setSearchParams],
  );

  const selectView = useCallback(
    (view: WorkspaceViewId) => setParams({ view }, { replace: true }),
    [setParams],
  );

  // One `["agent", slug]` and one `["agent-runs", slug]` for the whole screen.
  // The header, the loop bar and the body all want them; react-query dedupes
  // the keys, which is the only reason three regions polling at 5s is one poll.
  const {
    data: agent,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["agent", slug],
    queryFn: () => api.getAgent(slug),
    enabled: !!slug,
    refetchInterval: 5000,
  });

  const { data: runs = [] } = useQuery({
    queryKey: ["agent-runs", slug],
    queryFn: () => api.getAgentRuns(slug),
    enabled: !!slug,
    refetchInterval: 5000,
  });

  // Hoisted rather than reached through in the dependency lists: the compiler
  // infers the whole `agent` as the dependency and refuses to preserve a memo
  // whose declared one is narrower.
  const strategies = agent?.strategies;
  const sslug = useMemo(
    () => pickStrategy(strategies ?? [], runs, url.strategy),
    [strategies, runs, url.strategy],
  );
  const selectedRun = useMemo(
    () => pickRun(runs, sslug, url.run),
    [runs, sslug, url.run],
  );

  const { data: strategy = null } = useQuery({
    queryKey: ["strategy", slug, sslug],
    queryFn: () => api.getStrategy(slug, sslug!),
    enabled: !!slug && !!sslug,
    refetchInterval: 5000,
  });

  const { data: routineInstances = [] } = useQuery({
    queryKey: ["routine-instances"],
    queryFn: api.getRoutineInstances,
    enabled: showRoutinesBrowser,
    refetchInterval: 5000,
  });

  const deleteAgentMutation = useMutation({
    mutationFn: () => api.deleteAgent(slug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      navigate("/");
    },
  });

  /**
   * This surface cannot send a message, so it navigates to the workspace at `/`
   * carrying the request (FEAT-092). The encoding is not optional: an opener
   * carries backticks, parens and quotes.
   */
  const askAgent = useCallback(
    (text?: string) =>
      navigate(
        `/?agent=${encodeURIComponent(slug)}${
          text ? `&ask=${encodeURIComponent(text)}` : ""
        }`,
      ),
    [navigate, slug],
  );

  if (error && !agent) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="max-w-sm rounded-lg border border-red-500/30 bg-[var(--color-surface)] p-8 text-center">
          <AlertCircle className="mx-auto mb-3 h-10 w-10 text-[var(--color-red)]" />
          <h2 className="mb-1 text-lg font-semibold">Failed to Load Agent</h2>
          <p className="text-sm text-[var(--color-text-muted)]">
            {error instanceof Error ? error.message : "An unexpected error occurred."}
          </p>
          <Link
            to="/"
            className="mt-4 inline-flex items-center gap-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> Back to Agents
          </Link>
        </div>
      </div>
    );
  }

  if (isLoading || !agent) {
    return (
      <div className="flex h-64 items-center justify-center text-[var(--color-text-muted)]">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  const isRunning = (agent.strategies || []).some((st) => st.status === "running");
  const view = url.view;

  const body = isKnowledgeTab(view) ? (
    /* Keyed on the section so a playbook left open in Skills does not follow
       the reader into Memories — the reset `AgentKnowledge`'s own tab click
       does, for a host that clicks nothing. */
    <AgentKnowledge
      key={view}
      slug={agent.slug}
      layout="bare"
      tab={view}
      onAskAgent={askAgent}
      routinesAction={
        <div className="flex shrink-0 items-center gap-1.5">
          <button
            onClick={() => setShowRoutinesBrowser(true)}
            className="flex shrink-0 items-center gap-1 rounded border border-[var(--color-border)] px-2 py-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
            title="Every run these routines produced, and their reports"
          >
            <ScrollText className="h-3 w-3" /> Reports
          </button>
          <Link
            to={`/routines?agent=${agent.slug}`}
            className="flex shrink-0 items-center gap-1 px-1 py-1 text-[11px] text-[var(--color-text-muted)]/70 transition-colors hover:text-[var(--color-text)]"
            title="The full library, on its own page"
          >
            <ExternalLink className="h-3 w-3" /> Full library
          </Link>
        </div>
      }
      onOpenStrategy={(strategySlug) =>
        setParams({ view: "playbook", strategy: strategySlug })
      }
    />
  ) : !sslug ? (
    <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
      This agent has no strategies yet, so there is no loop to look at.
    </p>
  ) : view === "money" ? (
    <PerformancePanel slug={agent.slug} sslug={sslug} />
  ) : view === "playbook" || view === "now" ? (
    /* Step 1 of FEAT-103: Now is the workbench until it has a body of its own. */
    <StrategyWorkbench
      slug={agent.slug}
      sslug={sslug}
      onDeleted={() => setParams({ view: "strategies", strategy: null })}
    />
  ) : (
    <LabPointer slug={agent.slug} sslug={sslug} runId={selectedRun?.run_id} />
  );

  return (
    <div className="flex h-full min-h-0 w-full flex-col">
      <WorkspaceHeader
        agent={agent}
        strategy={strategy}
        isRunning={isRunning}
        onAskAgent={() => askAgent()}
        onDelete={() => setShowDeleteConfirm(true)}
      />

      <div className="flex min-h-0 flex-1">
        <WorkspaceSpine current={spineSectionFor(view)} onSelect={selectView} />
        <div className="min-w-0 flex-1 overflow-y-auto p-4">{body}</div>
      </div>

      {showRoutinesBrowser && (
        <ReportBrowser
          initialSourceTypeFilter={slug}
          instances={routineInstances}
          onClose={() => setShowRoutinesBrowser(false)}
        />
      )}

      <ConfirmDialog
        open={showDeleteConfirm}
        title="Delete Agent"
        isPending={deleteAgentMutation.isPending}
        isError={deleteAgentMutation.isError}
        errorText="Failed to delete agent. It may have running strategies."
        onConfirm={() => deleteAgentMutation.mutate()}
        onClose={() => setShowDeleteConfirm(false)}
      >
        Delete <strong className="text-[var(--color-text)]">{agent.name}</strong> and
        all its strategies? This cannot be undone.
      </ConfirmDialog>
    </div>
  );
}

/**
 * Where the run views live until they move in here.
 *
 * Step 1 ships the shell over today's bodies and deletes nothing, so Runs, Tick
 * and Fleet still point at the Lab. Step 2 promotes the tick spine and step 3
 * takes the Lab's whole job, at which point this goes.
 */
function LabPointer({
  slug,
  sslug,
  runId,
}: {
  slug: string;
  sslug: string;
  runId?: string;
}) {
  const query = new URLSearchParams({ strategy: sslug });
  if (runId) query.set("run", runId);
  return (
    <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
      <Link
        to={`/agents/${encodeURIComponent(slug)}/runs?${query}`}
        className="inline-flex items-center gap-1 text-[var(--color-primary)] hover:underline"
      >
        Read this agent's runs in the Lab <ExternalLink className="h-3 w-3" />
      </Link>
    </p>
  );
}
