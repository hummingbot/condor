/**
 * The Vaults tab: every vault on this chain, yours first, and the door to a new
 * one.
 *
 * A vault is a wallet that trades a strategy and is owned by a program rather
 * than by a person. Most of them are **private** — no token, no outside
 * holders, the runner deposits and withdraws at will — and that is a finished
 * state, not an unfinished one. Tokenizing is a separate, later, one-way
 * decision, so the list never nags about it.
 *
 * What a card must make unambiguous, because getting it wrong costs money:
 * which phase the vault is in, what the chain says its state is, and whether
 * the local record disagrees with the chain.
 *
 * **A vault is public, so this page is readable without a wallet.** A vault's
 * runner, state, phase and launch terms are on chain for anyone; connecting a
 * wallet does not reveal them, it only says which of them are yours. Gating the
 * list behind a connected-and-attached wallet hid chain data behind a local
 * file, and told someone whose browser held a different key that they could not
 * look — at their own vaults, on a public chain.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Plus, Vault as VaultIcon } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import { NoServerCard } from "@/components/NoServerCard";
import { isTokenized } from "@/components/vaults/format";
import {
  CopyAddress,
  PhaseBadge,
  StateBadge,
} from "@/components/vaults/shared";
import { useServer } from "@/hooks/useServer";
import { api, type VaultInfo } from "@/lib/api";
import { useWallet } from "@/lib/wallet/context";

/** One titled block of cards. Two of them at most: yours, and everyone's. */
function Section({
  title,
  count,
  children,
}: {
  title: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-6">
      <h2 className="mb-2 flex items-baseline gap-2 text-[12px] font-semibold tracking-wide text-[var(--color-text-muted)] uppercase">
        {title}
        <span className="font-normal normal-case">{count}</span>
      </h2>
      <div className="grid gap-3 sm:grid-cols-2">{children}</div>
    </section>
  );
}

function feePct(bps: number): string {
  return `${(bps / 100).toFixed(bps % 100 === 0 ? 0 : 1)}%`;
}

function VaultCard({ vault }: { vault: VaultInfo }) {
  const chain = vault.chain;
  const pin = vault.pin as { agent_ref?: Record<string, string> } | null;
  const agent = pin?.agent_ref?.agentSlug ?? pin?.agent_ref?.agent_slug;
  const strategy = pin?.agent_ref?.strategySlug ?? pin?.agent_ref?.strategy_slug;

  return (
    <Link
      to={`/vaults/${vault.account}`}
      className="block rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 transition-colors hover:bg-[var(--color-surface-hover)]"
    >
      <div className="mb-2 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold">{vault.label || "Untitled vault"}</h3>
          <CopyAddress address={vault.account} label="Swig account" />
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <PhaseBadge vault={vault} />
          <StateBadge vault={vault} />
        </div>
      </div>

      {vault.drift && (
        <p className="mb-2 flex items-start gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-700 dark:text-amber-300">
          <AlertTriangle className="mt-px h-3 w-3 shrink-0" />
          {vault.drift}. It will not run until this is resolved.
        </p>
      )}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-[var(--color-text-muted)]">
        {agent && (
          <>
            <dt>Strategy</dt>
            <dd className="text-right font-mono text-[var(--color-text)]">
              {agent}/{strategy}
            </dd>
          </>
        )}
        {chain && chain.version > 0 && (
          <>
            <dt>Version</dt>
            <dd className="text-right font-mono text-[var(--color-text)]">v{chain.version}</dd>
          </>
        )}
        {isTokenized(vault) && chain && (
          <>
            <dt title="The share of realised LP fees that buys and burns the token">Burn</dt>
            <dd className="text-right font-mono text-[var(--color-text)]">{feePct(chain.fee_bps)}</dd>
            <dt title="Circulating over max supply at launch">Issued</dt>
            <dd className="text-right font-mono text-[var(--color-text)]">
              {feePct(chain.issue_bps)}
            </dd>
          </>
        )}
        <dt>Delegate</dt>
        <dd className="text-right">
          {chain?.delegate ? (
            <span className="font-mono text-[var(--color-text)]">
              {chain.delegate.slice(0, 4)}…{chain.delegate.slice(-4)}
            </span>
          ) : (
            <span className="text-amber-600 dark:text-amber-400">none — it cannot trade</span>
          )}
        </dd>
      </dl>
    </Link>
  );
}

export function Vaults() {
  const { server } = useServer();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { attached, connected } = useWallet();

  const vaults = useQuery({
    queryKey: ["vaults", server],
    queryFn: () => api.listVaults(server!),
    enabled: !!server,
    retry: false,
  });

  // "Mine" is the key in the browser, not the one attached: it is the wallet
  // whose vaults you are looking for, and it answers before any signature.
  // Where they differ the wallet menu says so.
  const me = connected?.address ?? attached;
  const mine = (vaults.data ?? []).filter((vault) => vault.runner_address === me);
  const others = (vaults.data ?? []).filter((vault) => vault.runner_address !== me);

  const cleanup = useMutation({
    mutationFn: (account: string) => api.deleteVault(account),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["vaults", server] }),
  });

  if (!server) {
    return <NoServerCard message="Pick a server to see the vaults it runs." />;
  }

  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-5 flex items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold">Vaults</h1>
          <p className="text-[12px] text-[var(--color-text-muted)]">
            A wallet that trades a strategy and is owned by a program. Private until you decide
            otherwise.
          </p>
        </div>
        <button
          type="button"
          onClick={() => navigate("/vaults/new")}
          disabled={!attached}
          title={attached ? undefined : "Attach a wallet first — a vault's runner is a key"}
          className="inline-flex items-center gap-1.5 rounded-md bg-[var(--color-accent)] px-3 py-1.5 text-[12px] font-medium text-white disabled:opacity-50"
        >
          <Plus className="h-3.5 w-3.5" />
          New vault
        </button>
      </div>

      {vaults.isLoading && (
        <p className="text-[12px] text-[var(--color-text-muted)]">Reading the chain…</p>
      )}
      {vaults.error && (
        <p className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
          {(vaults.error as Error).message}
        </p>
      )}

      {!me && (vaults.data?.length ?? 0) > 0 && (
        <p className="mb-4 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-[12px] text-[var(--color-text-muted)]">
          Connect a wallet to see which of these are yours. Anyone can read them; only their
          runner&rsquo;s key can change one.
        </p>
      )}

      {vaults.data?.length === 0 && (
        <div className="rounded-lg border border-dashed border-[var(--color-border)] p-8 text-center">
          <VaultIcon className="mx-auto mb-3 h-8 w-8 text-[var(--color-text-muted)]" />
          <p className="text-sm font-medium">No vaults yet</p>
          <p className="mx-auto mt-1 max-w-sm text-[12px] text-[var(--color-text-muted)]">
            A new vault starts private: you fund it, it runs your strategy, and you can take the
            money out whenever you like.
          </p>
        </div>
      )}

      {mine.length > 0 && (
        <Section title="My vaults" count={mine.length}>
          {mine.map((vault) => (
            <div key={vault.account} className="relative">
              <VaultCard vault={vault} />
              {/* A record with no confirmed transaction behind it: the build
                  was never signed, or it never landed. There is nothing to
                  resume — creating a vault is one signature — so the only
                  thing to offer is forgetting it. */}
              {!vault.live && (
                <button
                  type="button"
                  onClick={() => cleanup.mutate(vault.account)}
                  className="absolute right-3 bottom-3 text-[10px] text-[var(--color-text-muted)] underline hover:text-[var(--color-text)]"
                >
                  discard
                </button>
              )}
            </div>
          ))}
        </Section>
      )}

      {others.length > 0 && (
        <Section title={mine.length > 0 ? "All vaults" : "Vaults on this chain"} count={others.length}>
          {others.map((vault) => (
            <VaultCard key={vault.account} vault={vault} />
          ))}
        </Section>
      )}
    </div>
  );
}
