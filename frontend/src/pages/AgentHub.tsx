import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Globe, Loader2, Lock, Store, Upload } from "lucide-react";
import { useState } from "react";

import {
  HubDeviceField,
  HubDeviceSignIn,
  PublishAgentButton,
  WithdrawListingButton,
} from "@/components/settings/AgentsSettings";
import { HubBrowser } from "@/components/settings/HubBrowser";
import { useIsAdmin } from "@/hooks/useIsAdmin";
import { api } from "@/lib/api";

/**
 * Botcamp Hub, as one destination reached from the Agents home (FEAT-153).
 *
 * Rendered by the Agents workspace itself, in the conversation's column, so
 * the rail and the dock stay put and it reads as part of the app rather than
 * a page you leave to — hence no "back" link: the rail is the way back.
 *
 * Before this, Hub browse and the Botcamp link lived under Settings → Agents,
 * and publish lived only on each agent's own workspace page — three doors to
 * one subject, one of them (withdraw) with no dashboard control at all. This
 * is the one page: Pull agents requires this install to already be linked to
 * Botcamp — the backend's own read endpoints accept an anonymous lookup, but
 * this dashboard asks for a signed-in install before it will use them, so
 * "connect to Botcamp" is the one gate every visitor meets before anything
 * else on this page works; publishing is admin-only, by `useIsAdmin`
 * (cosmetic — the server's own `require_admin` on each route is what actually
 * gates publish/withdraw).
 *
 * **Two jobs, two tabs.** Pulling somebody else's agent and publishing one of
 * yours are different errands that happen to share a hub, and the first cut
 * stacked them in one scrolling column: a catalogue search, a slug lookup and
 * an agent picker, three near-identical input rows with nothing but a hairline
 * between them. The tablist is the differentiator — one errand on screen at a
 * time — and the install's Botcamp identity, which belongs to neither errand
 * because it is install-wide, sits in the header above both.
 *
 * There is exactly one "Sign in to Botcamp" control here. The link is
 * install-wide, so a second door to it is not a second choice — it is the
 * same button twice, which is what the first cut of this page shipped: the
 * Pull gate opened a modal onto the device flow while the Botcamp section
 * rendered the identical flow inline underneath it. The two halves of that
 * flow are now split by state rather than by place: not linked is the Pull
 * tab's `HubDeviceSignIn`, linked is the header's account chip, and the chip
 * does not exist until `linked` is true — so signing in from the gate is also
 * what makes publishing appear.
 *
 * The admin query is mounted *only* inside the `isAdmin` branches, not merely
 * hidden behind them — `useIsAdmin` resolves with its own round trip, and
 * firing an admin-only request before that answers would be the one way this
 * page could leak that a non-admin seat exists at all. The Pull gate cannot
 * read the same admin-only `["hub-device"]` state for that reason (it would
 * be a 403 for every non-admin seat, on every visit) — it reads
 * `getHubDeviceLinked` instead, the one bit of that state every signed-in
 * seat is allowed to see, and `HubDeviceSignIn` mounts the admin-only state
 * machine inside the gate's own `isAdmin` branch.
 */
export function AgentHub() {
  const queryClient = useQueryClient();
  const isAdmin = useIsAdmin();
  const [tab, setTab] = useState<"pull" | "publish">("pull");
  const { data: hubDevice } = useQuery({
    queryKey: ["hub-device-linked"],
    queryFn: api.getHubDeviceLinked,
  });
  const linked = hubDevice?.linked ?? false;
  const canPublish = isAdmin && linked;

  // Publishing is reachable only once this install is linked and the seat is
  // an admin; a tab that cannot work says why instead of disappearing, so
  // "where do I publish mine?" has an answer before sign-in rather than after.
  const publishBlocked = !isAdmin
    ? "Only an admin can publish this install's agents"
    : !linked
      ? "Sign in to Botcamp to publish this install's agents"
      : "";
  const active = canPublish ? tab : "pull";

  return (
    <div className="mx-auto w-full max-w-5xl px-6 py-8">
      <header className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 text-xl font-bold text-[var(--color-text)]">
            <Globe className="h-5 w-5" />
            Agent Hub
          </h1>
          <p className="mt-1 text-sm text-[var(--color-text-muted)]">
            Botcamp's catalogue of published agents, and this install's own
            publishing.
          </p>
        </div>
        {/* The link is install-wide, so it belongs to the page and not to
            either tab. Mounted only when linked: the not-linked half of the
            same state machine is the Pull tab's gate, and rendering both
            would be two sign-in buttons for one link. */}
        {isAdmin && linked && (
          <div className="shrink-0 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2">
            <HubDeviceField />
          </div>
        )}
      </header>

      <div
        role="tablist"
        aria-label="Agent Hub sections"
        className="mt-6 flex items-stretch gap-1.5 border-b border-[var(--color-border)] pb-3"
      >
        <HubTab
          id="pull"
          label="Pull agents"
          icon={<Store className="h-3.5 w-3.5" />}
          active={active === "pull"}
          onSelect={() => setTab("pull")}
        />
        <HubTab
          id="publish"
          label="Publish yours"
          icon={<Upload className="h-3.5 w-3.5" />}
          active={active === "publish"}
          disabledReason={publishBlocked}
          onSelect={() => setTab("publish")}
        />
      </div>

      {active === "pull" ? (
        <section
          role="tabpanel"
          id="hub-panel-pull"
          aria-labelledby="hub-tab-pull"
          className="pt-5"
        >
          <p className="mb-4 max-w-2xl text-xs leading-relaxed text-[var(--color-text-muted)]">
            Agents other people published on Botcamp. Installing one goes
            through the same review as a file: every executable is named before
            anything is written, the routines and skills arrive switched off,
            and nothing is started.
          </p>
          {linked ? (
            <HubBrowser
              onInstalled={() =>
                queryClient.invalidateQueries({ queryKey: ["agents"] })
              }
            />
          ) : (
            <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-[var(--color-border)] px-4 py-10 text-center">
              <Lock className="h-4 w-4 text-[var(--color-text-muted)]" />
              <p className="text-sm text-[var(--color-text)]">
                Sign in to Botcamp to look up or pull agents
                {isAdmin ? ", and to publish this install's own" : ""}.
              </p>
              {isAdmin ? (
                <HubDeviceSignIn />
              ) : (
                <p className="text-xs text-[var(--color-text-muted)]">
                  Ask an admin to connect this install to Botcamp.
                </p>
              )}
            </div>
          )}
        </section>
      ) : (
        <section
          role="tabpanel"
          id="hub-panel-publish"
          aria-labelledby="hub-tab-publish"
          className="pt-5"
        >
          <h2 className="mb-1 text-sm font-bold text-[var(--color-text)]">
            Upload yours
          </h2>
          <p className="mb-4 max-w-2xl text-xs leading-relaxed text-[var(--color-text-muted)]">
            Choose one of this install's agents to publish. Publishing acts
            under the Botcamp profile above, install-wide, and sends the agent's
            files off this box for review — withdrawing removes the listing, it
            does not recall anybody's download.
          </p>
          <UploadYours />
        </section>
      )}
    </div>
  );
}

/**
 * One tab of the hub, in the dashboard's own filled-pill idiom (the same one
 * the agent workspace's sections use) rather than an underline: the fill is
 * what makes the errand you are on legible at a glance.
 */
function HubTab({
  id,
  label,
  icon,
  active,
  disabledReason = "",
  onSelect,
}: {
  id: string;
  label: string;
  icon: React.ReactNode;
  active: boolean;
  disabledReason?: string;
  onSelect: () => void;
}) {
  return (
    <button
      id={`hub-tab-${id}`}
      role="tab"
      aria-selected={active}
      aria-controls={`hub-panel-${id}`}
      disabled={Boolean(disabledReason)}
      title={disabledReason || label}
      onClick={onSelect}
      className={`flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-semibold transition-colors ${
        active
          ? "border-[var(--color-primary)]/40 bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
          : "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text-muted)] hover:border-[var(--color-primary)]/40 hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-[var(--color-border)] disabled:hover:bg-[var(--color-surface)] disabled:hover:text-[var(--color-text-muted)]"
      }`}
    >
      {icon}
      {label}
      {disabledReason && <Lock className="h-3 w-3" />}
    </button>
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
          className="w-72 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
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
          className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3"
        >
          <span className="flex min-w-0 items-center gap-2 text-sm text-[var(--color-text)]">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[var(--color-primary)]/10 text-[var(--color-primary)]">
              <Brain className="h-3.5 w-3.5" />
            </span>
            <span className="truncate font-mono text-xs">{slug}</span>
          </span>
          <span className="flex flex-wrap items-center gap-2">
            <PublishAgentButton slug={slug} />
            <WithdrawListingButton slug={slug} />
          </span>
        </div>
      )}
    </div>
  );
}
