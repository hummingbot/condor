/**
 * The connected-account card: which key this is, what it holds, and the two
 * things you can do about it.
 *
 * Balances, tokens and history come from ConnectorKit, which reads the cluster
 * the provider configured — the node this server's Gateway uses, so what the
 * card shows is the chain a signature from here would land on.
 *
 * One section no other app's wallet menu has: **attached**. Connecting a wallet
 * and being that wallet are different facts here. A vault's runner is checked
 * on chain against the key that attached, and the browser can be connected to
 * any other one — so when they disagree the card says so at the top, because
 * the signature would be rejected for a reason invisible from the outside.
 */
import { useBalance, useTokens } from "@solana/connector/react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Check, Coins, Copy, Globe, LogOut, RefreshCw } from "lucide-react";
import { useState } from "react";

import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";
import { useWallet } from "@/lib/wallet/context";

import { shortAddress } from "./address";
import { AddressAvatar, Spinner } from "./primitives";

export function WalletMenu({ onDone }: { onDone: () => void }) {
  const { server } = useServer();
  const { connected, attached, mismatched, disconnect, attach, detach } = useWallet();
  const [copied, setCopied] = useState(false);
  const [tokensOpen, setTokensOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const address = connected?.address ?? attached ?? "";
  const balance = useBalance();
  const tokens = useTokens();

  // Which chain this is. The same answer the Settings badge gives and the same
  // one the provider built the cluster from — it changes what every number
  // above it means.
  const chain = useQuery({
    queryKey: ["gateway-chain", server],
    queryFn: () => api.getGatewayChain(server!),
    enabled: !!server,
    retry: false,
    staleTime: 60_000,
  });

  // `formatted` is the library's own rendering of amount-over-decimals; using
  // it beats dividing here, where a wrong decimals guess shows a wrong number
  // rather than an error.
  const held = (tokens.tokens ?? []).filter((token) => token.amount > 0n);

  const copy = async () => {
    await navigator.clipboard.writeText(address);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };

  const run = async (action: () => Promise<void>) => {
    setError(null);
    setBusy(true);
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="w-80 p-3">
      {mismatched && (
        <p className="mb-3 flex items-start gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/10 px-2.5 py-2 text-[11px] text-amber-700 dark:text-amber-300">
          <AlertTriangle className="mt-px h-3 w-3 shrink-0" />
          <span>
            This account is attached to {shortAddress(attached!)}. You can look at anything; only
            that key can sign for its vaults.
          </span>
        </p>
      )}

      <div className="mb-3 flex items-start gap-3">
        <AddressAvatar address={address} className="h-10 w-10" />
        <div className="min-w-0 flex-1">
          <p className="truncate font-mono text-[15px] font-semibold">{shortAddress(address)}</p>
          <p className="text-[11px] text-[var(--color-text-muted)]">
            {connected?.name ?? "attached, not connected"}
          </p>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={copy}
            title="Copy address"
            className="rounded-full border border-[var(--color-border)] p-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
          </button>
          <span
            title={
              chain.data
                ? `${chain.data.kind === "surfpool" ? "surfpool fork" : "mainnet"} · ${chain.data.rpc_host}`
                : "chain unknown"
            }
            className="relative rounded-full border border-[var(--color-border)] p-1.5 text-[var(--color-text-muted)]"
          >
            <Globe className="h-3.5 w-3.5" />
            <span
              className={`absolute right-0.5 bottom-0.5 h-1.5 w-1.5 rounded-full ${
                chain.data
                  ? chain.data.kind === "surfpool"
                    ? "bg-amber-400"
                    : "bg-emerald-500"
                  : "bg-[var(--color-text-muted)]"
              }`}
            />
          </span>
        </div>
      </div>

      <div className="mb-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2.5">
        <div className="flex items-center justify-between">
          <span className="text-[11px] text-[var(--color-text-muted)]">Holds</span>
          <button
            type="button"
            onClick={() => void balance.refetch()}
            title="Read it again"
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            {balance.isLoading ? <Spinner /> : <RefreshCw className="h-3 w-3" />}
          </button>
        </div>
        <div className="mt-0.5 flex items-baseline justify-between gap-2">
          <span className="font-mono text-xl font-semibold">
            {balance.error ? "—" : balance.formattedSol}
          </span>
          <span className="text-[12px] text-[var(--color-text-muted)]">SOL</span>
        </div>
      </div>

      {held.length > 0 && (
        <div className="mb-3 rounded-xl border border-[var(--color-border)]">
          <button
            type="button"
            onClick={() => setTokensOpen((v) => !v)}
            className="flex w-full items-center justify-between px-3 py-2 text-[12px] font-medium"
          >
            <span className="flex items-center gap-2">
              <Coins className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
              Tokens
            </span>
            <span className="text-[var(--color-text-muted)]">{held.length}</span>
          </button>
          {tokensOpen && (
            <dl className="max-h-48 overflow-y-auto border-t border-[var(--color-border)] px-3 py-2">
              {held.map((token) => (
                <div key={token.mint} className="flex items-baseline justify-between gap-3 py-0.5">
                  <dt className="truncate text-[12px]">
                    {token.symbol ?? `${token.mint.slice(0, 4)}…`}
                  </dt>
                  <dd className="font-mono text-[12px] text-[var(--color-text-muted)]">
                    {token.formatted}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      )}

      {error && <p className="mb-2 text-[11px] text-[var(--color-red)]">{error}</p>}

      {/* Attaching is Condor's own step: it proves to this account that the
          browser holds this key, which is what every vault action is checked
          against. Connecting alone proves nothing to anyone but the browser. */}
      {connected && !attached && (
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            run(async () => {
              await attach();
              onDone();
            })
          }
          className="mb-2 w-full rounded-lg bg-[var(--color-accent)] px-3 py-2 text-[12px] font-medium text-white disabled:opacity-60"
        >
          {busy ? "Signing…" : `Attach ${shortAddress(connected.address)} to this account`}
        </button>
      )}
      {attached && (
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            run(async () => {
              await detach();
              onDone();
            })
          }
          className="mb-2 w-full rounded-lg border border-[var(--color-border)] px-3 py-2 text-[12px] font-medium hover:bg-[var(--color-surface-hover)] disabled:opacity-60"
        >
          Detach {shortAddress(attached)} from this account
        </button>
      )}

      {connected && (
        <button
          type="button"
          onClick={() => {
            disconnect();
            onDone();
          }}
          className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-[var(--color-border)] px-3 py-2 text-[12px] font-medium text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
        >
          <LogOut className="h-3.5 w-3.5" />
          Disconnect this browser
        </button>
      )}
    </div>
  );
}
