import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2, Lock, Save } from "lucide-react";
import { useState } from "react";

import { useServer } from "@/hooks/useServer";
import { OWNER_ONLY_HINT, useServerPermission } from "@/hooks/useServerPermission";
import { api, errorStatus, type ApiClientConfigUpdate } from "@/lib/api";

/**
 * Settings → Hummingbot API: what the navbar-selected server runs, and the client
 * defaults every bot deployed from it next will inherit (FEAT-121).
 *
 * Scoped by `useServer()` alone, like the Gateway and Keys panels beside it — the navbar
 * select is the only picker, so this panel and the rest of the app can never disagree
 * about which server is "the" server. `server` is in every query key, so switching
 * servers refetches rather than showing the previous one's answers.
 */

/** 501: this server's API predates these routes. An upgrade, not an outage. */
const TOO_OLD = 501;

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <h3 className="mb-3 text-sm font-medium text-[var(--color-text)]">{title}</h3>
      {children}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5 text-sm">
      <span className="shrink-0 text-[var(--color-text-muted)]">{label}</span>
      <span className="min-w-0 break-all text-right font-mono text-xs text-[var(--color-text)]">
        {children}
      </span>
    </div>
  );
}

/**
 * Why a request failed, in the operator's terms.
 *
 * A 501 is the one failure that is not a failure: the server is healthy and this panel
 * is newer than it, so it gets a neutral notice naming the fix. Everything else is red.
 */
function Problem({ error }: { error: unknown }) {
  const tooOld = errorStatus(error) === TOO_OLD;
  const message = error instanceof Error ? error.message : "Request failed";
  return (
    <div
      className={`flex items-start gap-2 rounded-md border p-3 text-xs ${
        tooOld
          ? "border-[var(--color-border)] text-[var(--color-text-muted)]"
          : "border-red-500/30 text-[var(--color-red)]"
      }`}
    >
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>{message}</span>
    </div>
  );
}

/** A human label for a MARKET_DATA_* key: `ticker_max_age` → `Ticker max age`. */
function humanize(key: string): string {
  const words = key.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function VersionCard({ server }: { server: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["api-server-info", server],
    queryFn: () => api.getApiServerInfo(server),
  });

  if (isLoading) {
    return (
      <Card title="Version">
        <Loader2 className="h-4 w-4 animate-spin text-[var(--color-text-muted)]" />
      </Card>
    );
  }
  if (error) {
    return (
      <Card title="Version">
        <Problem error={error} />
      </Card>
    );
  }
  if (!data) return null;

  const container = data.container;
  // A locally built image has no registry digest: nothing published it, so there is no
  // content address to compare the tag against.
  const digest = container?.digest;
  const shortDigest = digest ? digest.slice(digest.indexOf("sha256:")).slice(0, 19) : null;

  return (
    <Card title="Version">
      <div className="divide-y divide-[var(--color-border)]">
        <Row label="hummingbot-api">{data.api_version}</Row>
        <Row label="hummingbot">{data.hummingbot_version ?? "not installed"}</Row>
        <Row label="Image">{container?.image ?? "unknown"}</Row>
        <Row label="Digest">{shortDigest ?? "locally built"}</Row>
        {container?.compose_project && (
          <Row label="Compose project">{container.compose_project}</Row>
        )}
      </div>

      {data.pinned === true && (
        <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-500/30 p-2 text-xs text-amber-500">
          <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>Pinned{data.pinned_reason ? ` — ${data.pinned_reason}` : ""}</span>
        </div>
      )}
      {data.pinned === null && (
        <p className="mt-3 text-xs text-[var(--color-text-muted)]">
          Could not reach the Docker daemon on this server, so whether the image is pinned
          is unknown.
        </p>
      )}
    </Card>
  );
}

/** The fields a person has typed into; absent means "whatever the server says". */
interface Draft {
  source?: string;
  tokenName?: string;
  tokenSymbol?: string;
  sharePct?: string;
}

function BotDefaultsCard({ server }: { server: string }) {
  const qc = useQueryClient();
  // Predicts the refusal only; `_require_owner` on PUT /settings/api/client-config is
  // what enforces it.
  const { isOwner } = useServerPermission();

  const { data, isLoading, error } = useQuery({
    queryKey: ["api-client-config", server],
    queryFn: () => api.getApiClientConfig(server),
  });

  // Only the fields actually typed into, laid over the server's answer for display.
  // The alternative — copying the answer into state when it arrives — needs an effect
  // that writes state on every refetch, and would quietly overwrite a half-typed field
  // the moment a background refetch landed.
  const [draft, setDraft] = useState<Draft>({});
  const edit = (patch: Draft) => setDraft((d) => ({ ...d, ...patch }));

  const saveMut = useMutation({
    mutationFn: (changes: ApiClientConfigUpdate) =>
      api.updateApiClientConfig(server, changes),
    onSuccess: () => {
      // Drop the draft first: once the refetch lands, the server's answer *is* the form.
      setDraft({});
      qc.invalidateQueries({ queryKey: ["api-client-config", server] });
    },
  });

  if (isLoading) {
    return (
      <Card title="Bot defaults">
        <Loader2 className="h-4 w-4 animate-spin text-[var(--color-text-muted)]" />
      </Card>
    );
  }
  if (error) {
    return (
      <Card title="Bot defaults">
        <Problem error={error} />
      </Card>
    );
  }
  if (!data) return null;

  const savedSource = data.rate_oracle_source?.name ?? "";
  const savedTokenName = data.global_token?.global_token_name ?? "";
  const savedTokenSymbol = data.global_token?.global_token_symbol ?? "";
  const savedSharePct = String(data.rate_limits_share_pct ?? "");

  const source = draft.source ?? savedSource;
  const tokenName = draft.tokenName ?? savedTokenName;
  const tokenSymbol = draft.tokenSymbol ?? savedTokenSymbol;
  const sharePct = draft.sharePct ?? savedSharePct;

  const share = Number(sharePct);
  const shareValid = sharePct !== "" && Number.isFinite(share) && share > 0 && share <= 100;

  // Only what actually changed is sent, so a save that moves the token does not write
  // back whatever else the form happened to be holding.
  const changes: ApiClientConfigUpdate = {};
  if (source !== savedSource) changes.rate_oracle_source = source;
  if (tokenName !== savedTokenName) changes.global_token_name = tokenName;
  if (tokenSymbol !== savedTokenSymbol) changes.global_token_symbol = tokenSymbol;
  if (share !== data.rate_limits_share_pct) changes.rate_limits_share_pct = share;

  const dirty = Object.keys(changes).length > 0;
  const canSave = isOwner && dirty && shareValid && tokenName.trim() !== "";
  const field =
    "w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-sm text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-50";

  return (
    <Card title="Bot defaults">
      <div className="space-y-3">
        <label className="block">
          <span className="text-xs text-[var(--color-text-muted)]">Rate oracle source</span>
          <select
            value={source}
            onChange={(e) => edit({ source: e.target.value })}
            disabled={!isOwner}
            title={isOwner ? undefined : OWNER_ONLY_HINT}
            aria-label="Rate oracle source"
            className={field}
          >
            {/* The server's own list: which sources exist is whatever its bundled
                hummingbot knows. A source already persisted but absent from that list
                still appears, so the form never silently changes it on save. */}
            {!data.available_sources.includes(source) && source !== "" && (
              <option value={source}>{source}</option>
            )}
            {data.available_sources.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-xs text-[var(--color-text-muted)]">Global token</span>
            <input
              value={tokenName}
              onChange={(e) => edit({ tokenName: e.target.value })}
              disabled={!isOwner}
              title={isOwner ? undefined : OWNER_ONLY_HINT}
              aria-label="Global token"
              className={field}
            />
          </label>
          <label className="block">
            <span className="text-xs text-[var(--color-text-muted)]">Token symbol</span>
            <input
              value={tokenSymbol}
              onChange={(e) => edit({ tokenSymbol: e.target.value })}
              disabled={!isOwner}
              title={isOwner ? undefined : OWNER_ONLY_HINT}
              aria-label="Token symbol"
              className={field}
            />
          </label>
        </div>

        <label className="block">
          <span className="text-xs text-[var(--color-text-muted)]">
            Rate limit share (%)
          </span>
          <input
            type="number"
            min={1}
            max={100}
            value={sharePct}
            onChange={(e) => edit({ sharePct: e.target.value })}
            disabled={!isOwner}
            title={isOwner ? undefined : OWNER_ONLY_HINT}
            aria-label="Rate limit share (%)"
            className={field}
          />
          {!shareValid && sharePct !== "" && (
            <span className="text-xs text-[var(--color-red)]">
              Must be more than 0 and at most 100.
            </span>
          )}
        </label>

        <div className="flex items-center justify-between gap-3 pt-1">
          <p className="text-xs text-[var(--color-text-muted)]">
            Applies to bots deployed after saving. Running bots keep their current
            settings.
          </p>
          <button
            onClick={() => saveMut.mutate(changes)}
            disabled={!canSave || saveMut.isPending}
            title={isOwner ? undefined : OWNER_ONLY_HINT}
            className="flex shrink-0 items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-[var(--color-primary)]/80 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saveMut.isPending ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Save className="h-3 w-3" />
            )}
            Save
          </button>
        </div>

        {!isOwner && (
          <p className="text-xs text-[var(--color-text-muted)]">{OWNER_ONLY_HINT}</p>
        )}
        {saveMut.error != null && <Problem error={saveMut.error} />}
      </div>
    </Card>
  );
}

function MarketDataCard({ server }: { server: string }) {
  const { data, error } = useQuery({
    queryKey: ["api-server-info", server],
    queryFn: () => api.getApiServerInfo(server),
  });

  // The version card above already reports the failure; a second copy of it says
  // nothing new.
  if (error || !data) return null;

  const entries = Object.entries(data.market_data ?? {});
  if (entries.length === 0) return null;

  return (
    <Card title="Market data">
      <div className="divide-y divide-[var(--color-border)]">
        {entries.map(([key, value]) => (
          <Row key={key} label={humanize(key)}>
            {String(value)}
          </Row>
        ))}
      </div>
      <p className="mt-3 text-xs text-[var(--color-text-muted)]">
        Read-only. Set in the server&apos;s <code>.env</code> as <code>MARKET_DATA_*</code>;
        takes effect on API restart.
      </p>
    </Card>
  );
}

export function ApiServerSettings() {
  const { server } = useServer();

  if (!server) {
    return (
      <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
        Select a server in the top bar.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <VersionCard server={server} />
      {/* Keyed by server: a draft typed for one server must not survive a switch to
          another, where it would read as that server's current values. */}
      <BotDefaultsCard key={server} server={server} />
      <MarketDataCard server={server} />
    </div>
  );
}
