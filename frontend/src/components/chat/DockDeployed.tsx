import { ExternalLink } from "lucide-react";
import { Link } from "react-router-dom";

import { DeploymentLedger } from "@/components/agent/lab/DeploymentLedger";
import { useConversationDeployments } from "@/components/chat/deployedPanel";
import { WorkspaceSheet } from "@/components/chat/WorkspaceSheet";
import { CHAT_SLUG } from "@/lib/api";

/**
 * What this conversation put into the world (FEAT-110).
 *
 * You ask Condor to deploy something, it says it did, and until this panel the
 * only way to find out whether it is alive and what it has made was to leave
 * the chat for `/bots` and pick it out of thirty-four rows that all look alike.
 * The agent's own Lab has had exactly this table since FEAT-100 — for loop runs
 * only, because loop runs were the only runs that recorded anything. FEAT-105
 * made a conversation record the same things a run records; this spends that.
 *
 * A thin host on purpose: the query, {@link DeploymentLedger} unchanged, and the
 * two empty states. The ledger is the same component reading the same
 * `DeploymentRow` wire the Lab reads, so the answer to "what did this run do"
 * looks the same wherever the run happened.
 */
export function DockDeployed({
  conversationId,
  agentSlug,
  onClose,
}: {
  conversationId: string;
  /** Who answered — `""` is not "no agent", it is the default one, Condor. */
  agentSlug: string;
  onClose: () => void;
}) {
  const { data, isPending, isError } =
    useConversationDeployments(conversationId);
  const rows = data?.deployments ?? [];
  const runKey = `${agentSlug || CHAT_SLUG}.chat`;

  return (
    <WorkspaceSheet
      title="Deployed"
      subtitle="What this conversation put into the world"
      actions={
        // The whole of this chat's kind of work, where each row's own link is
        // that one record. Not narrowed to this conversation: `?run=` addresses
        // a loop's session number and a chat has none, so the honest scope is
        // the chat run key rather than a parameter the browser would ignore.
        <Link
          to={`/bots?scope=${encodeURIComponent(`agent:${runKey}`)}`}
          className="inline-flex items-center gap-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-primary)]"
        >
          See chat deployments in the fleet
          <ExternalLink className="h-3 w-3" />
        </Link>
      }
      onClose={onClose}
    >
      {isPending ? (
        <p className="text-[11px] text-[var(--color-text-muted)]">Reading…</p>
      ) : isError ? (
        <p className="text-[11px] text-[var(--color-text-muted)]">
          Could not read what this conversation deployed.
        </p>
      ) : rows.length > 0 ? (
        <DeploymentLedger rows={rows} />
      ) : (
        <EmptyState predates={!!data?.predates_ledger} />
      )}
    </WorkspaceSheet>
  );
}

/**
 * The two ways a conversation can have nothing to show, said apart.
 *
 * They look identical — an empty table either way — and they mean opposite
 * things. A conversation that ran before FEAT-105 could have deployed a fleet
 * and left no trace of it, so telling its reader "nothing happened here" would
 * be a confident lie about the one thing this panel exists to answer.
 */
function EmptyState({ predates }: { predates: boolean }) {
  return (
    <p className="text-[11px] leading-relaxed text-[var(--color-text-muted)]">
      {predates ? (
        <>
          Condor wasn&rsquo;t recording what it did when this conversation ran,
          so there is nothing to show — not even to say it deployed nothing.
          Conversations from here on keep their own record.
        </>
      ) : (
        <>This conversation hasn&rsquo;t deployed anything yet.</>
      )}
    </p>
  );
}
