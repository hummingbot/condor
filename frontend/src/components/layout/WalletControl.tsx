/**
 * The header's wallet control: connect, attach, and see which key is signing.
 *
 * It sits beside the server selector because it answers the same kind of
 * question — *whose* is this, right now. A vault's runner is a key rather than
 * a login, so the address shown here is the one the chain will check, and the
 * control is deliberately loud when the browser's wallet and the attached one
 * disagree: signing with the wrong account produces a transaction the program
 * rejects, and the reason would otherwise be invisible.
 *
 * Nothing renders at all when no wallet is installed and none is attached, so
 * the header does not grow a permanent control for a feature most installs will
 * never use.
 */
import { Wallet } from "lucide-react";
import { useState } from "react";

import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import { useWallet } from "@/lib/wallet/context";

const short = (address: string) => `${address.slice(0, 4)}…${address.slice(-4)}`;

export function WalletControl() {
  const { available, connected, attached, mismatched, connect, disconnect, attach } = useWallet();
  const [open, setOpen] = useState(false);
  // State rather than a ref: the menu positions itself against this element
  // during render, and a ref read there is a value React has not promised is
  // current. The same shape the DEX chain selector uses.
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);

  if (!connected && !attached && available.length === 0) return null;

  const tone = mismatched
    ? "border-amber-500/50 text-amber-600 dark:text-amber-400"
    : "border-[var(--color-border)] text-[var(--color-text-muted)]";

  return (
    <div className="relative">
      <button
        ref={setAnchor}
        type="button"
        onClick={() => setOpen((v) => !v)}
        title={
          mismatched
            ? "This browser's wallet is not the one attached to this account"
            : attached
              ? `Signing as ${attached}`
              : "Connect a wallet"
        }
        className={`inline-flex items-center gap-1.5 rounded-md border bg-[var(--color-bg)] px-2 py-1 text-[11px] font-medium transition-colors hover:text-[var(--color-text)] ${tone}`}
      >
        <Wallet className="h-3.5 w-3.5" />
        {connected ? short(connected.address) : attached ? short(attached) : "Connect"}
      </button>

      <AnchoredMenu
        anchor={anchor}
        open={open}
        onClose={() => setOpen(false)}
        align="right"
        className="w-64"
      >
        <div className="p-1 text-[12px]">
            {mismatched && (
              <p className="mb-1 rounded bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-700 dark:text-amber-300">
                Connected to a different account than the one attached. Switch in your wallet, or
                the chain will reject what you sign.
              </p>
            )}
            {!connected &&
              available.map((wallet) => (
                <button
                  key={wallet.name}
                  type="button"
                  onClick={() => {
                    connect(wallet.name);
                    setOpen(false);
                  }}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left hover:bg-[var(--color-surface-hover)]"
                >
                  {wallet.icon && <img src={wallet.icon} alt="" className="h-4 w-4" />}
                  {wallet.name}
                </button>
              ))}
            {connected && !attached && (
              <button
                type="button"
                onClick={() => {
                  attach();
                  setOpen(false);
                }}
                className="w-full rounded px-2 py-1.5 text-left hover:bg-[var(--color-surface-hover)]"
              >
                Attach this wallet to Condor
              </button>
            )}
            {connected && (
              <button
                type="button"
                onClick={() => {
                  disconnect();
                  setOpen(false);
                }}
                className="w-full rounded px-2 py-1.5 text-left text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
              >
                Disconnect this browser
              </button>
            )}
            {attached && (
              <p className="mt-1 border-t border-[var(--color-border)] px-2 pt-1.5 font-mono text-[10px] text-[var(--color-text-muted)]">
                attached: {attached}
              </p>
            )}
        </div>
      </AnchoredMenu>
    </div>
  );
}
