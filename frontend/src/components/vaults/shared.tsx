/**
 * The pieces every vault surface repeats: the two-phase badge, a state pill,
 * an address, and the wallet gate.
 *
 * They live here rather than in one page because the list, the create flow and
 * the detail page all have to say the same thing about a vault's phase, and a
 * second spelling of "private" is how the product ends up describing one object
 * two ways.
 */
import { Copy, Lock, ShieldCheck, Wallet } from "lucide-react";
import { useState } from "react";

import { WalletPicker } from "@/components/wallet/WalletPicker";
import type { VaultInfo } from "@/lib/api";
import { useWallet } from "@/lib/wallet/context";

import { isTokenized, shortAddress } from "./format";

export function CopyAddress({ address, label }: { address: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard.writeText(address).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        });
      }}
      title={label ? `${label}: ${address}` : address}
      className="inline-flex items-center gap-1 font-mono text-[11px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
    >
      {shortAddress(address)}
      <Copy className="h-3 w-3" />
      {copied && <span className="text-[10px]">copied</span>}
    </button>
  );
}

/**
 * Private or tokenized, said plainly.
 *
 * This is the most consequential fact about a vault and the one a reader is
 * most likely to get wrong, because "private" sounds like a visibility setting
 * and is actually a statement about who can take the money out. So the badge
 * carries the consequence, not the adjective.
 */
export function PhaseBadge({ vault }: { vault: VaultInfo }) {
  if (isTokenized(vault)) {
    return (
      <span
        title="Tokenized: nobody can withdraw, including the runner. Holders redeem after a wind-down."
        className="inline-flex items-center gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--color-text-muted)]"
      >
        <ShieldCheck className="h-3 w-3" />
        Tokenized
      </span>
    );
  }
  return (
    <span
      title="Private: no token and no outside holders, so the runner can deposit and withdraw freely."
      className="inline-flex items-center gap-1 rounded-md border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400"
    >
      <Lock className="h-3 w-3" />
      Private
    </span>
  );
}

const STATE_TONE: Record<string, string> = {
  Running: "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  Paused: "border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text-muted)]",
  WindingDown: "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  Redeemable: "border-sky-500/40 bg-sky-500/10 text-sky-600 dark:text-sky-400",
};

export function StateBadge({ vault }: { vault: VaultInfo }) {
  // No chain state means the create transaction never landed. Not a
  // half-finished vault — creating one is a single signature — so there is
  // nothing to continue, only a row to discard.
  if (!vault.chain) {
    return (
      <span
        title="The transaction that would have created this vault never landed."
        className="rounded-md border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400"
      >
        never created
      </span>
    );
  }
  const state = vault.chain.state;
  return (
    <span
      className={`rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${STATE_TONE[state] ?? STATE_TONE.Paused}`}
    >
      {state === "WindingDown" ? "Winding down" : state}
    </span>
  );
}

/**
 * What to show when there is no attached wallet.
 *
 * A vault's runner is a key, not an account: everything the runner may do is
 * checked on chain against it, so Condor genuinely cannot act for someone who
 * has not proved which key is theirs. Saying that is better than a disabled
 * button with no explanation.
 */
export function WalletGate({ children }: { children: React.ReactNode }) {
  const { attached, connected, connect, attach, available, mismatched } = useWallet();
  const [picking, setPicking] = useState(false);

  if (attached && connected && !mismatched) return <>{children}</>;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-6 text-center">
      <Wallet className="mx-auto mb-3 h-8 w-8 text-[var(--color-text-muted)]" />
      <h2 className="mb-1 text-base font-semibold">
        {mismatched
          ? "Connect the wallet you attached"
          : attached
            ? "Connect the wallet you attached"
            : "Attach a wallet"}
      </h2>
      <p className="mx-auto mb-4 max-w-md text-sm text-[var(--color-text-muted)]">
        {mismatched ? (
          <>
            This browser is connected to <CopyAddress address={connected!.address} />, and this
            account signs as <CopyAddress address={attached!} />. Only that key can sign what this
            page builds — switch accounts in your wallet, or detach from the wallet menu.
          </>
        ) : attached ? (
          <>
            This account signs as <CopyAddress address={attached} />. Connect that wallet to
            continue.
          </>
        ) : (
          <>
            A vault&rsquo;s runner is a key, not a login. Condor never holds one — you sign in your
            own wallet, and the chain checks the signature.
          </>
        )}
      </p>
      <div className="flex flex-wrap items-center justify-center gap-2">
        {!connected && (
          <button
            type="button"
            onClick={() => setPicking(true)}
            className="rounded-md bg-[var(--color-accent)] px-3 py-1.5 text-[12px] font-medium text-white"
          >
            Connect wallet
          </button>
        )}
        {connected && !attached && (
          <button
            type="button"
            onClick={() => attach()}
            className="rounded-md bg-[var(--color-accent)] px-3 py-1.5 text-[12px] font-medium text-white"
          >
            Attach {shortAddress(connected.address)}
          </button>
        )}
      </div>
      {picking && (
        <WalletPicker
          available={available}
          onConnect={connect}
          onClose={() => setPicking(false)}
        />
      )}
    </div>
  );
}
