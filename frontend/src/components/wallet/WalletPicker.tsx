/**
 * The wallet picker: one row per wallet this browser can actually offer.
 *
 * A dialog rather than a menu because connecting is a decision with
 * consequences — the key chosen here is the one the chain will check — and
 * because the list has to have room for the case where there is nothing in it.
 *
 * Two groups, and the split is not cosmetic. Installed wallets hold a key the
 * person owns. **Dev keypairs hold a secret that is in the page bundle**, exist
 * only when the chain endpoint has said `surfpool`, and are labelled as what
 * they are so that nobody reaches for one twice.
 */
import { X } from "lucide-react";
import { useEffect, useState } from "react";

import type { AvailableWallet } from "@/lib/wallet/context";

import { lastConnected } from "./address";

import { Spinner, WalletAvatar } from "./primitives";

/** Where to send someone whose browser has no Solana wallet at all. */
const INSTALL = [
  { name: "Phantom", url: "https://phantom.app" },
  { name: "Solflare", url: "https://solflare.com" },
  { name: "Backpack", url: "https://backpack.app" },
];

const isDev = (wallet: AvailableWallet) => wallet.name.startsWith("dev:");

export function WalletPicker({
  available,
  onConnect,
  onClose,
}: {
  available: AvailableWallet[];
  onConnect: (id: string) => Promise<void>;
  onClose: () => void;
}) {
  const [connecting, setConnecting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const recent = lastConnected();

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const choose = async (id: string) => {
    setError(null);
    setConnecting(id);
    try {
      await onConnect(id);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setConnecting(null);
    }
  };

  const installed = available.filter((w) => !isDev(w));
  const dev = available.filter(isDev);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-label="Connect your wallet"
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-xl"
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold">Connect your wallet</h2>
            <p className="mt-0.5 text-[12px] text-[var(--color-text-muted)]">
              A vault&rsquo;s runner is a key, not a login. Condor never holds
              one.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-md p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {error && (
          <p className="mb-3 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-[12px] text-[var(--color-red)]">
            {error}
          </p>
        )}

        <div className="space-y-2">
          {installed.map((wallet) => (
            <WalletRow
              key={wallet.id}
              wallet={wallet}
              recent={wallet.id === recent}
              busy={connecting === wallet.id}
              onClick={() => choose(wallet.id)}
            />
          ))}
        </div>

        {dev.length > 0 && (
          <>
            <p className="mt-4 mb-2 text-[11px] font-medium tracking-wide text-amber-600 uppercase dark:text-amber-400">
              Dev keypairs — this fork only
            </p>
            <div className="space-y-2">
              {dev.map((wallet) => (
                <WalletRow
                  key={wallet.id}
                  wallet={wallet}
                  label={wallet.name.slice("dev:".length)}
                  recent={wallet.id === recent}
                  busy={connecting === wallet.id}
                  onClick={() => choose(wallet.id)}
                />
              ))}
            </div>
            <p className="mt-2 text-[11px] text-[var(--color-text-muted)]">
              Their secrets are in this page&rsquo;s bundle. They appear only
              because the chain endpoint said surfpool.
            </p>
          </>
        )}

        {available.length === 0 && (
          <div className="rounded-xl border border-dashed border-[var(--color-border)] p-5 text-center">
            <p className="text-[13px] font-medium">
              No Solana wallet in this browser
            </p>
            <p className="mx-auto mt-1 mb-3 max-w-xs text-[12px] text-[var(--color-text-muted)]">
              Install one, then reopen this. Condor talks to the Wallet
              Standard, so any of these works.
            </p>
            <div className="flex flex-wrap justify-center gap-2">
              {INSTALL.map((entry) => (
                <a
                  key={entry.name}
                  href={entry.url}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-[12px] hover:bg-[var(--color-surface-hover)]"
                >
                  {entry.name}
                </a>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function WalletRow({
  wallet,
  label,
  recent,
  busy,
  onClick,
}: {
  wallet: AvailableWallet;
  label?: string;
  recent: boolean;
  busy: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className="flex w-full items-center justify-between gap-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] px-4 py-3 text-left transition-colors hover:bg-[var(--color-surface-hover)] disabled:opacity-60"
    >
      <span className="flex items-center gap-2">
        <span className="text-[14px] font-medium">{label ?? wallet.name}</span>
        {recent && (
          <span className="rounded-full bg-[var(--color-surface-hover)] px-2 py-0.5 text-[10px] text-[var(--color-text-muted)]">
            Recent
          </span>
        )}
      </span>
      {busy ? (
        <Spinner className="h-5 w-5" />
      ) : (
        <WalletAvatar src={wallet.icon} alt="" className="h-8 w-8" />
      )}
    </button>
  );
}
