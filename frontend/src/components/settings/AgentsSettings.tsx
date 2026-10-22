import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Brain,
  Check,
  Download,
  ExternalLink,
  Loader2,
  Upload,
} from "lucide-react";
import { useState } from "react";

import { InlineConfirm } from "@/components/ui/InlineConfirm";
import { api } from "@/lib/api";

import { ImportAgentDialog } from "./ImportAgentDialog";

/**
 * Where an agent arrives from somewhere else, and where one leaves.
 *
 * It needs a home of its own rather than a button hung off an existing one:
 * the dashboard has no "New Agent" affordance at all — agents are created
 * through chat — so there was nowhere to put Import. Export is also offered on
 * each agent's own workspace header, beside Delete; this panel is the place
 * that answers "how do I move an agent" without first knowing which one.
 *
 * The hub — browsing/installing what other people published, connecting a
 * Botcamp profile and publishing — moved to its own `/hub` page (FEAT-153).
 * This is local file transfer only now; `HubLinkField`, `PublishAgentButton`
 * and `WithdrawListingButton` below are exported for that page to compose.
 */
export function AgentsSettings() {
  const queryClient = useQueryClient();
  const { data: agents, isLoading } = useQuery({
    queryKey: ["agents"],
    queryFn: api.getAgents,
  });

  return (
    <div className="space-y-8">
      <section>
        <h2 className="mb-1 text-sm font-bold text-[var(--color-text)]">
          Import an agent
        </h2>
        <p className="mb-3 text-xs text-[var(--color-text-muted)]">
          An agent travels as one file. Importing reads it, shows you what is in it and
          registers the agent with its routines and skills switched off — nothing runs
          until you switch them on.
        </p>
        <ImportAgentDialog
          onImported={() => queryClient.invalidateQueries({ queryKey: ["agents"] })}
        />
      </section>

      <section className="border-t border-[var(--color-border)] pt-8">
        <h2 className="mb-1 text-sm font-bold text-[var(--color-text)]">
          Export an agent
        </h2>
        <p className="mb-3 text-xs text-[var(--color-text-muted)]">
          The file carries the agent itself — its AGENT.md, skills, routines, controllers
          and loops. Its memory, journals, session history and this install's server pin
          stay here.
        </p>
        {isLoading ? (
          <Loader2 className="h-4 w-4 animate-spin text-[var(--color-text-muted)]" />
        ) : (
          <ul className="space-y-1.5">
            {(agents ?? []).map((agent) => (
              <li
                key={agent.slug}
                className="flex items-center justify-between gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2"
              >
                <span className="flex min-w-0 items-center gap-2">
                  <Brain className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
                  <span className="truncate text-sm text-[var(--color-text)]">
                    {agent.name}
                  </span>
                  <span className="truncate font-mono text-[11px] text-[var(--color-text-muted)]">
                    {agent.slug}
                  </span>
                </span>
                <ExportAgentButton slug={agent.slug} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/**
 * Connect this install to a Botcamp profile with a pairing code (FEAT-148).
 *
 * Replaces the pasted-token UX: starting a pairing shows a verification
 * string and opens Botcamp's approval page in a new tab, where the signed-in
 * admin compares that same string before accepting. This component only ever
 * polls Condor's own `/hub-link` — the credential itself is stored
 * server-side the moment the hub hands it over and never reaches this
 * component or any other response this dashboard reads.
 *
 * Composed on the admin-only part of `/hub` now, not Settings (FEAT-153).
 */
export function HubLinkField() {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["hub-link"],
    queryFn: api.getHubLink,
    refetchInterval: (query) =>
      query.state.data?.status === "pending" ? 3000 : false,
  });

  const start = useMutation({
    mutationFn: api.startHubLink,
    onSuccess: (started) => {
      queryClient.setQueryData(["hub-link"], {
        status: "pending",
        code: started.code,
        verification: started.verification,
        expires_at: started.expires_at,
        link_url: started.link_url,
        profile_handle: "",
      });
      window.open(started.link_url, "_blank", "noopener,noreferrer");
    },
  });

  const disconnect = useMutation({
    mutationFn: api.disconnectHubLink,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["hub-link"] }),
  });

  const status = data?.status ?? "none";
  const busy = start.isPending || disconnect.isPending;

  return (
    <div className="space-y-1.5">
      {status === "linked" && (
        <div className="flex items-center gap-2">
          <p
            data-hub-link-status="linked"
            className="flex items-center gap-1 text-xs text-[var(--color-text)]"
          >
            <Check className="h-3.5 w-3.5 text-[var(--color-primary)]" />
            Connected to Botcamp as{" "}
            <span className="font-mono">{data?.profile_handle}</span>
          </p>
          <button
            onClick={() => disconnect.mutate()}
            disabled={busy}
            className="text-xs text-[var(--color-text-muted)] underline hover:text-[var(--color-red)] disabled:opacity-50"
          >
            Disconnect
          </button>
        </div>
      )}

      {status === "pending" && (
        <div className="space-y-1.5" data-hub-link-status="pending">
          <p className="text-xs text-[var(--color-text)]">
            Compare this string with the one shown on Botcamp, then accept there:
          </p>
          <div className="flex items-center gap-2">
            <span className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 font-mono text-sm text-[var(--color-text)]">
              {data?.verification}
            </span>
            <a
              href={data?.link_url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-xs font-semibold text-[var(--color-primary)] hover:underline"
            >
              Open Botcamp <ExternalLink className="h-3 w-3" />
            </a>
            <button
              onClick={() => disconnect.mutate()}
              disabled={busy}
              className="text-xs text-[var(--color-text-muted)] underline hover:text-[var(--color-red)] disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
          <p className="flex items-center gap-1.5 text-[11px] text-[var(--color-text-muted)]">
            <Loader2 className="h-3 w-3 animate-spin" /> Waiting for approval on
            Botcamp…
          </p>
        </div>
      )}

      {(status === "none" || status === "denied" || status === "expired") && (
        <div className="space-y-1" data-hub-link-status={status}>
          <button
            onClick={() => start.mutate()}
            disabled={busy}
            className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
          >
            {start.isPending ? "Starting…" : "Connect to Botcamp"}
          </button>
          {status === "denied" && (
            <p className="text-[11px] text-[var(--color-red)]">
              The pairing was denied on Botcamp.
            </p>
          )}
          {status === "expired" && (
            <p className="text-[11px] text-[var(--color-text-muted)]">
              That pairing expired before it was accepted.
            </p>
          )}
        </div>
      )}

      {(start.isError || disconnect.isError) && (
        <p role="alert" data-hub-link-error className="text-xs text-[var(--color-red)]">
          {(start.error ?? disconnect.error)?.message}
        </p>
      )}
    </div>
  );
}

/**
 * Download one agent's bundle.
 *
 * The endpoint is bearer-guarded, so this fetches the text and makes an object
 * URL rather than pointing an `<a href>` at it — a link cannot carry the
 * header. The URL is revoked straight after the click, since the blob is only
 * needed for the instant the browser takes to save it.
 */
export function ExportAgentButton({ slug }: { slug: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setBusy(true);
    setError(null);
    try {
      const text = await api.exportAgentBundle(slug);
      const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `${slug}.condor-agent.json`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="flex items-center gap-2">
      {error && (
        <span role="alert" data-export-error className="text-xs text-[var(--color-red)]">
          {error}
        </span>
      )}
      <button
        onClick={download}
        disabled={busy}
        title={`Export ${slug} as one file`}
        className="flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)] disabled:opacity-50"
      >
        {busy ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Download className="h-3.5 w-3.5" />
        )}
        Export
      </button>
    </span>
  );
}

/**
 * Publish one agent to the hub, after showing what will be uploaded, and
 * track its review from receipt through verdict (FEAT-150).
 *
 * Beside Export because it is the same act with a different destination — and
 * the destination is the whole difference, which is why this one confirms and
 * Export does not: a download stays on this box, and a publish cannot be
 * unpublished out of somebody else's copy.
 *
 * **The preview is the export call.** It fetches `GET /agents/{slug}/bundle` —
 * the identical bytes the publish will send, built by the identical function —
 * and lists what is in them. That is not only "the same preview" in spirit: it
 * means FEAT-144's refusal for a value that can only be a secret surfaces
 * here, before the Publish button is ever offered, because `build` is what
 * refuses and both doors call it.
 *
 * **Publishing does not mean published.** The hub answers an upload with a
 * pending submission, not a listing — this component polls
 * `GET /agents/{slug}/submission` (which itself polls the hub only while the
 * stored record is still pending) so a reload still shows a review in
 * flight, a rejection with its reasons, or the eventual listing and badge.
 */
export function PublishAgentButton({ slug }: { slug: string }) {
  const queryClient = useQueryClient();
  const [preview, setPreview] = useState<{ files: string[]; bytes: number; version: number } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  const submission = useQuery({
    queryKey: ["hub-submission", slug],
    queryFn: () => api.getHubSubmission(slug),
    refetchInterval: (query) =>
      query.state.data?.receipt?.status === "pending" ? 4000 : false,
  });

  const [busy, setBusy] = useState(false);
  const receipt = submission.data?.receipt ?? null;

  async function look() {
    setBusy(true);
    setError(null);
    try {
      const text = await api.exportAgentBundle(slug);
      const bundle = JSON.parse(text) as {
        version: number;
        files: { path: string }[];
      };
      setPreview({
        files: bundle.files.map((f) => f.path),
        bytes: new TextEncoder().encode(text).length,
        version: bundle.version,
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.publishAgentToHub(slug);
      setPreview(null);
      queryClient.setQueryData(["hub-submission", slug], { receipt: result.receipt });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // A resubmission is offered once the last one reached a verdict — a fresh
  // Publish always starts a new submission (§5.4 of the contract), never a
  // retry of the same one.
  const canPublish = !preview && (receipt === null || receipt.status !== "pending");

  return (
    <span className="flex items-center gap-2">
      {(error || submission.isError) && (
        <span role="alert" data-publish-error className="text-xs text-[var(--color-red)]">
          {error ?? (submission.error as Error | undefined)?.message}
        </span>
      )}

      {receipt?.status === "pending" && (
        <span data-publish-status="pending" className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Pending review ({receipt.submission_id})
        </span>
      )}

      {receipt?.status === "rejected" && !preview && (
        <span data-publish-status="rejected" className="text-xs text-[var(--color-red)]">
          Rejected
          {receipt.reasons.length > 0 && (
            <>: {receipt.reasons.map((r) => r.detail).join(" ")}</>
          )}
        </span>
      )}

      {receipt?.status === "published" && !preview && (
        <span data-publish-status="published" className="flex items-center gap-1 text-xs text-[var(--color-text-muted)]">
          <Check className="h-3.5 w-3.5 text-[var(--color-primary)]" />
          Published as <span className="font-mono">{receipt.listing_id}</span>
          {receipt.badge && ` (${receipt.badge})`}
        </span>
      )}

      {preview ? (
        <span
          data-publish-preview
          className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-2.5 py-1 text-[11px] text-[var(--color-text-muted)]"
        >
          <span>
            Uploading version {preview.version} — {preview.files.length} file
            {preview.files.length !== 1 ? "s" : ""}, {preview.bytes} bytes. This
            leaves your machine and cannot be recalled from anybody's download.
          </span>
          <button
            onClick={send}
            disabled={busy}
            className="shrink-0 rounded border border-[var(--color-primary)]/40 bg-[var(--color-primary)]/10 px-2 py-0.5 font-semibold text-[var(--color-primary)] disabled:opacity-50"
          >
            {busy ? "Publishing…" : "Publish"}
          </button>
          <button
            onClick={() => setPreview(null)}
            disabled={busy}
            className="shrink-0 underline hover:text-[var(--color-text)] disabled:opacity-50"
          >
            Cancel
          </button>
        </span>
      ) : (
        canPublish && (
          <button
            onClick={look}
            disabled={busy}
            title={`Publish ${slug} to the agent hub`}
            className="flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)] disabled:opacity-50"
          >
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Upload className="h-3.5 w-3.5" />
            )}
            {receipt ? "Publish again" : "Publish"}
          </button>
        )
      )}
    </span>
  );
}

/**
 * Warn when Botcamp later delisted or re-badged the listing this agent was
 * installed from (CORR-812). Renders nothing for an agent never installed
 * from the hub, and nothing once a confirmed check still reads ordinary —
 * this is a thing to notice, not a steady-state status line.
 *
 * `live_check_failed` is its own, quieter branch rather than the warning:
 * the server already refused to turn an unreachable hub into a moderation
 * claim (that is the whole point of `GET /{slug}/hub-status` existing), so
 * the UI must not do it either. `checked_at` is always the last time the hub
 * gave a *confirmed* answer, shown in both branches, since that is the fact
 * an operator deciding whether to trust this warning actually needs.
 */
export function HubInstallStatus({ slug }: { slug: string }) {
  const status = useQuery({
    queryKey: ["hub-agent-status", slug],
    queryFn: () => api.getAgentHubStatus(slug),
    // A background fact, not something the operator is watching resolve in
    // real time the way a pending submission is — no need for the faster
    // poll `PublishAgentButton` uses above.
    refetchInterval: 5 * 60 * 1000,
  });

  const data = status.data;
  if (!data?.tracked) return null;

  if (data.status === "delisted" || data.status === "badge_changed") {
    return (
      <span
        data-hub-status={data.status}
        title={`Last confirmed with Botcamp: ${data.checked_at || "never"}`}
        className="flex items-center gap-1.5 text-xs text-[var(--color-red)]"
      >
        <AlertTriangle className="h-3.5 w-3.5" />
        {data.status === "delisted"
          ? "Botcamp withdrew this listing. The files installed here are unaffected — review before trusting this agent further."
          : `Botcamp changed this listing's badge since install (now ${data.badge || "unbadged"}). Review before trusting this agent further.`}
      </span>
    );
  }

  if (data.live_check_failed) {
    return (
      <span
        data-hub-status="unknown"
        title={`Last confirmed with Botcamp: ${data.checked_at || "never"}`}
        className="text-xs text-[var(--color-text-muted)]"
      >
        Could not reach Botcamp to recheck this listing.
      </span>
    );
  }

  return null;
}

/**
 * Withdraw a published listing from the hub (contract §5.4, follow-up to
 * FEAT-150's publish flow).
 *
 * Reads the same `["hub-submission", slug]` query `PublishAgentButton` keeps
 * warm rather than carrying its own copy of the receipt — the two mount
 * together on `/hub`'s Upload yours section, and a second fetch of the same
 * resource would just be a chance for them to disagree. Renders nothing until
 * that query says `published`: there is nothing to withdraw before then, and
 * a pending or rejected submission has no listing id to send.
 *
 * Withdrawing removes the listing; it does not recall anybody's download —
 * the two-step `InlineConfirm` exists so that sentence is read before the
 * call goes out, not after.
 */
export function WithdrawListingButton({ slug }: { slug: string }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submission = useQuery({
    queryKey: ["hub-submission", slug],
    queryFn: () => api.getHubSubmission(slug),
  });
  const receipt = submission.data?.receipt ?? null;

  const withdraw = useMutation({
    mutationFn: (listingId: string) => api.unpublishFromHub(listingId),
    onSuccess: () => {
      setConfirming(false);
      queryClient.invalidateQueries({ queryKey: ["hub-submission", slug] });
      queryClient.invalidateQueries({ queryKey: ["hub-agents"] });
    },
    onError: (e) => setError((e as Error).message),
  });

  if (receipt?.status !== "published") return null;

  return (
    <span className="flex items-center gap-2">
      {error && (
        <span role="alert" data-withdraw-error className="text-xs text-[var(--color-red)]">
          {error}
        </span>
      )}
      <InlineConfirm
        confirming={confirming}
        onRequest={() => {
          setError(null);
          setConfirming(true);
        }}
        onConfirm={() => withdraw.mutate(receipt.listing_id)}
        onCancel={() => setConfirming(false)}
        pending={withdraw.isPending}
        triggerLabel={`Withdraw ${slug} from the hub`}
        confirmLabel="Confirm withdraw — does not recall anybody's download"
        cancelLabel="Cancel withdraw"
      />
    </span>
  );
}
