import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Brain, Globe, Loader2, Lock } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import {
  BotcampSignInModal,
  HubDeviceField,
  PublishAgentButton,
  WithdrawListingButton,
} from "@/components/settings/AgentsSettings";
import { HubBrowser } from "@/components/settings/HubBrowser";
import { useIsAdmin } from "@/hooks/useIsAdmin";
import { api } from "@/lib/api";

/**
 * Botcamp Hub, as one destination reached from the Agents home (FEAT-153).
 *
 * Before this, Hub browse and the Botcamp link lived under Settings → Agents,
 * and publish lived only on each agent's own workspace page — three doors to
 * one subject, one of them (withdraw) with no dashboard control at all. This
 * is the one page: Pull agents requires this install to already be linked to
 * Botcamp — the backend's own read endpoints accept an anonymous lookup, but
 * this dashboard asks for a signed-in install before it will use them, so
 * "connect to Botcamp" is the one gate every visitor meets before anything
 * else on this page works; the Botcamp section — connecting this install and
 * uploading yours — is admin-only, by `useIsAdmin` (cosmetic — the server's
 * own `require_admin` on each route is what actually gates publish/withdraw),
 * and shows a sign-in step until this install is linked (PR #111's device
 * login), its normal content — connected status and the upload picker — only
 * once it is.
 *
 * The admin query is mounted *only* inside the `isAdmin` branch, not merely
 * hidden behind it — `useIsAdmin` resolves with its own round trip, and firing
 * an admin-only request before that answers would be the one way this page
 * could leak that a non-admin seat exists at all. The Pull gate cannot read
 * the same admin-only `["hub-device"]` state for that reason (it would be a
 * 403 for every non-admin seat, on every visit) — it reads
 * `getHubDeviceLinked` instead, the one bit of that state every signed-in
 * seat is allowed to see.
 *
 * Signing in itself happens in `BotcampSignInModal`, opened from the Pull
 * gate without leaving the page, closing itself the moment the poll reports
 * `"linked"` — at which point `linked` below flips too and the lookup form
 * replaces the gate. The Botcamp section further down keeps its own inline
 * `HubDeviceField` besides: that one also manages disconnect and Upload
 * yours, which the modal does not.
 */
export function AgentHub() {
  const queryClient = useQueryClient();
  const isAdmin = useIsAdmin();
  const [signInOpen, setSignInOpen] = useState(false);
  const { data: hubDevice } = useQuery({
    queryKey: ["hub-device-linked"],
    queryFn: api.getHubDeviceLinked,
  });
  const linked = hubDevice?.linked ?? false;

  return (
    <div className="mx-auto max-w-4xl space-y-8 px-4 py-6">
      <div>
        <Link
          to="/"
          className="flex items-center gap-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to Agents
        </Link>
        <h1 className="mt-2 flex items-center gap-2 text-xl font-bold text-[var(--color-text)]">
          <Globe className="h-5 w-5" />
          Agent Hub
        </h1>
        <p className="mt-1 text-sm text-[var(--color-text-muted)]">
          Botcamp's catalogue of published agents, and this install's own publishing.
        </p>
      </div>

      <section>
        <h2 className="mb-1 text-sm font-bold text-[var(--color-text)]">
          Pull agents
        </h2>
        <p className="mb-3 text-xs text-[var(--color-text-muted)]">
          Agents other people published, looked up by slug. Installing one goes
          through the same review as a file: every executable is named before
          anything is written, the routines and skills arrive switched off, and
          nothing is started.
        </p>
        {linked ? (
          <HubBrowser
            onInstalled={() => queryClient.invalidateQueries({ queryKey: ["agents"] })}
          />
        ) : (
          <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-[var(--color-border)] px-4 py-8 text-center">
            <Lock className="h-4 w-4 text-[var(--color-text-muted)]" />
            <p className="text-sm text-[var(--color-text)]">
              Sign in to Botcamp to look up or pull agents.
            </p>
            {isAdmin ? (
              <button
                onClick={() => setSignInOpen(true)}
                className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              >
                Sign in to Botcamp
              </button>
            ) : (
              <p className="text-xs text-[var(--color-text-muted)]">
                Ask an admin to connect this install to Botcamp.
              </p>
            )}
          </div>
        )}
      </section>

      {isAdmin && (
        <BotcampSignInModal open={signInOpen} onClose={() => setSignInOpen(false)} />
      )}

      {isAdmin && (
        <section className="border-t border-[var(--color-border)] pt-8">
          <h2 className="mb-1 text-sm font-bold text-[var(--color-text)]">Botcamp</h2>
          <p className="mb-3 text-xs text-[var(--color-text-muted)]">
            Publishing acts under one Botcamp profile, install-wide: every publish
            is made as that account.
          </p>
          <HubDeviceField>
            <div className="space-y-3 pt-4">
              <h3 className="text-xs font-bold text-[var(--color-text)]">
                Upload yours
              </h3>
              <p className="text-xs text-[var(--color-text-muted)]">
                Choose one of this install's agents to publish. Publishing sends
                its files off this box for review — withdrawing removes the
                listing, it does not recall anybody's download.
              </p>
              <UploadYours />
            </div>
          </HubDeviceField>
        </section>
      )}
    </div>
  );
}

/**
 * The admin's publish/withdraw flow, composed on top of the agent-page
 * components rather than reimplemented: `PublishAgentButton` already carries
 * the preview-then-send gate and the submission state, and
 * `WithdrawListingButton` reads the same `["hub-submission", slug]` query it
 * keeps warm, so there is exactly one place either can disagree about where a
 * submission stands.
 */
function UploadYours() {
  const [slug, setSlug] = useState("");
  const { data: agents, isLoading } = useQuery({
    queryKey: ["agents"],
    queryFn: api.getAgents,
  });

  return (
    <div className="space-y-3">
      {isLoading ? (
        <Loader2 className="h-4 w-4 animate-spin text-[var(--color-text-muted)]" />
      ) : (
        <select
          aria-label="Choose an agent to publish"
          value={slug}
          onChange={(e) => setSlug(e.target.value)}
          className="w-64 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
        >
          <option value="">Choose an agent…</option>
          {(agents ?? []).map((agent) => (
            <option key={agent.slug} value={agent.slug}>
              {agent.name} ({agent.slug})
            </option>
          ))}
        </select>
      )}

      {slug && (
        <div
          data-upload-chosen={slug}
          className="flex flex-wrap items-center gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2.5"
        >
          <span className="flex items-center gap-2 text-sm text-[var(--color-text)]">
            <Brain className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
            <span className="font-mono text-xs">{slug}</span>
          </span>
          <PublishAgentButton slug={slug} />
          <WithdrawListingButton slug={slug} />
        </div>
      )}
    </div>
  );
}
