import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowLeft } from "lucide-react";
import { useCallback, useState } from "react";
import {
  Link,
  Navigate,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";

import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import { AgentWorkspaceBody } from "@/components/agent/workspace/AgentWorkspaceBody";
import { WorkspaceHeader } from "@/components/agent/workspace/WorkspaceHeader";
import {
  runsRedirect,
  strategyRedirect,
} from "@/components/agent/workspace/views";
import { useWorkspaceUrl } from "@/components/agent/workspace/workspaceUrl";
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
 * What is left in this file is the *host*, and only the host: the slug off the
 * path, the two states a page can be in that a pane cannot (failed to load, and
 * a delete this agent is confirming), the header, and the binding of the
 * workspace's grammar to this route's search string. Everything under the
 * header is {@link AgentWorkspaceBody}, which the chat's pane mounts from the
 * home's search string instead (FEAT-117) — so this page and that pane are the
 * same screen at two widths rather than a page and a preview of it.
 */
export function AgentWorkspace() {
  const { slug = "" } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  // This route's own search string is the whole of the workspace's grammar.
  const adapter = useWorkspaceUrl(searchParams, setSearchParams);

  // The header's, and the two guards below. The same `["agent", slug]` the body
  // reads, so react-query serves both from one poll rather than two.
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

  return (
    <>
      <AgentWorkspaceBody
        slug={slug}
        adapter={adapter}
        /* The loop controls in this header act on the strategy the body
           resolved from the URL, so the body hands it back rather than the
           page picking a scope of its own for the two to disagree about. */
        header={({ strategy }) => (
          <WorkspaceHeader
            agent={agent}
            strategy={strategy}
            isRunning={isRunning}
            onAskAgent={() => askAgent()}
            onDelete={() => setShowDeleteConfirm(true)}
          />
        )}
        onAskAgent={askAgent}
      />

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
    </>
  );
}

/**
 * `/agents/:slug/runs` — the Lab's address, kept resolving.
 *
 * A redirect and not a deletion: it is in notification payloads, in the chat's
 * route facts and in whatever anyone has bookmarked. The query string travels
 * with it, so `?strategy=&run=&tick=` lands on exactly the run and the tick it
 * always did — the Lab's grammar is a subset of the workspace's, which is what
 * made the merge possible at all.
 */
export function AgentRunsRedirect() {
  const { slug = "" } = useParams<{ slug: string }>();
  const { search } = useLocation();
  return <Navigate to={runsRedirect(slug, search)} replace />;
}

/** `/agents/:slug/strategies/:sslug` — the strategy page's address, likewise. */
export function AgentStrategyRedirect() {
  const { slug = "", sslug = "" } = useParams<{ slug: string; sslug: string }>();
  const { search } = useLocation();
  return <Navigate to={strategyRedirect(slug, sslug, search)} replace />;
}
