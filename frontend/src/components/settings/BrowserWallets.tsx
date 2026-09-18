/**
 * The wallet this browser is holding — as opposed to the ones Gateway holds a
 * key for, listed below it.
 *
 * The distinction is the point of having two sections. A **Gateway wallet** is
 * a key on the server: it signs unattended, which is what lets a strategy trade
 * while nobody is watching, and anyone who can reach that server can spend
 * through it. A **browser wallet** is a key in an extension on this machine: it
 * signs only in front of its owner, which is why a vault's creator is one.
 *
 * Solana only, because that is the only chain anything here signs for. An
 * Ethereum browser wallet would be an address in a list and nothing else.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Star } from "lucide-react";
import { useState } from "react";

import { InlineConfirm } from "@/components/ui/InlineConfirm";
import { ConnectWalletButton } from "@/components/wallet/ConnectWalletButton";
import { AddressAvatar } from "@/components/wallet/primitives";
import { api } from "@/lib/api";
import { useWallet } from "@/lib/wallet/context";

/** The one chain a browser wallet means anything on here. */
export const BROWSER_CHAIN = "solana";

export function BrowserWallets() {
  const { connected, attached, mismatched, attach, detach } = useWallet();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const wallet = useQuery({
    queryKey: ["wallet"],
    queryFn: () => api.getWallet(),
    retry: false,
  });

  const attachMut = useMutation({
    mutationFn: () => attach(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["wallet"] }),
    onError: (e: Error) => setError(e.message),
  });

  const preferMut = useMutation({
    mutationFn: () => api.setPreferredWallet(BROWSER_CHAIN, "browser"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["wallet"] }),
    onError: (e: Error) => setError(e.message),
  });

  // Detaching lives here rather than in the wallet menu, which is an account
  // card for the key this browser is holding. Forgetting a key you are *not*
  // holding is exactly the case that menu cannot serve, and it is the case
  // someone who has switched wallets is in.
  const detachMut = useMutation({
    mutationFn: () => detach(),
    onSuccess: () => {
      setConfirming(false);
      queryClient.invalidateQueries({ queryKey: ["wallet"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const isDefault = wallet.data?.preferred?.[BROWSER_CHAIN] === "browser";

  return (
    <div>
      <h3 className="mb-2 text-xs font-semibold tracking-wider text-[var(--color-text-muted)] uppercase">
        Browser wallets
      </h3>

      {attached ? (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
          <div className="flex min-w-0 items-center gap-3">
            <AddressAvatar address={attached} className="h-8 w-8" />
            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                <span className="text-xs font-medium text-[var(--color-text)]">Solana</span>
                {isDefault && (
                  <span
                    className="flex items-center gap-0.5 rounded bg-[var(--color-primary)]/10 px-1.5 py-0.5 text-[10px] font-medium text-[var(--color-primary)]"
                    title="What Condor means by your Solana wallet"
                  >
                    <Star className="h-2.5 w-2.5" /> default
                  </span>
                )}
                <span className="text-[10px] text-[var(--color-text-muted)]">
                  {mismatched
                    ? "this browser is holding a different key"
                    : connected
                      ? `connected · ${connected.name}`
                      : "not connected in this browser"}
                </span>
              </div>
              <p className="truncate font-mono text-xs text-[var(--color-text-muted)]">
                {attached}
              </p>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-1">
            {!isDefault && !confirming && (
              <button
                type="button"
                onClick={() => {
                  setError(null);
                  preferMut.mutate();
                }}
                disabled={preferMut.isPending}
                title="Make this the wallet Condor means on Solana"
                className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-primary)] disabled:opacity-50"
              >
                {preferMut.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Star className="h-3.5 w-3.5" />
                )}
              </button>
            )}
            <InlineConfirm
              confirming={confirming}
              onRequest={() => {
                setError(null);
                setConfirming(true);
              }}
              onConfirm={() => detachMut.mutate()}
              onCancel={() => setConfirming(false)}
              pending={detachMut.isPending}
              triggerLabel="Detach this wallet from this account"
              confirmLabel="Confirm detach"
              cancelLabel="Cancel detach"
            />
          </div>
        </div>
      ) : (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
          <p className="mb-2 text-xs text-[var(--color-text-muted)]">
            {connected
              ? "This browser is holding a key. Prove it is yours and Condor will use it as your Solana wallet."
              : "No browser wallet here yet. A vault's creator is one of these — Condor never holds the key."}
          </p>
          {connected ? (
            <button
              type="button"
              onClick={() => {
                setError(null);
                attachMut.mutate();
              }}
              disabled={attachMut.isPending}
              className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
            >
              {attachMut.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
              Attach {connected.address.slice(0, 4)}…{connected.address.slice(-4)}
            </button>
          ) : (
            <ConnectWalletButton label="Connect Solana wallet" />
          )}
        </div>
      )}

      {error && <p className="mt-2 text-xs text-[var(--color-red)]">{error}</p>}
    </div>
  );
}
