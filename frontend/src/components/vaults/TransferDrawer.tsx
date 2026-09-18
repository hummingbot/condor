/**
 * Moving one asset between a runner's wallet and their vault, in either
 * direction.
 *
 * One drawer for both because they are one decision — how much of what, and
 * which way — even though the two directions are not the same mechanism and
 * the copy says so:
 *
 * * **In** is an ordinary transfer the runner's own wallet signs. Gateway
 *   builds it so the destination is derived from the Swig rather than typed;
 *   a vault has two addresses that look equally plausible to paste.
 * * **Out** is the *delegate* moving what it can already move. There is no
 *   withdraw instruction in the program — installing a delegate is what grants
 *   this — so it executes on the server and needs no signature here.
 *
 * Which is also why the flip button is not cosmetic: it changes who signs.
 *
 * Only while the vault is private. Once it has holders the money is not the
 * runner's to move and the only way out is a redemption after a wind-down; the
 * caller is responsible for not offering this then.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowUpDown, Loader2, X } from "lucide-react";
import { useEffect, useState } from "react";

import { shortAddress } from "@/components/vaults/format";
import { AddressAvatar } from "@/components/wallet/primitives";
import { api, type VaultBuild, type VaultInfo } from "@/lib/api";
import { useWallet } from "@/lib/wallet/context";

/** The asset being moved. `mint` is absent for native SOL, which is what the
 *  deposit and withdraw routes both take to mean "the chain's own money". */
export interface TransferAsset {
  symbol: string;
  mint?: string;
  /** What the vault holds of it, for the MAX button when moving out. */
  vaultAmount: number;
}

type Direction = "in" | "out";

const fmt = (n: number, max = 6) => n.toLocaleString(undefined, { maximumFractionDigits: max });

/** Mounted only while something is being moved, so every open starts clean —
 *  an amount left over from the last asset is the one thing nobody wants
 *  carried forward, and unmounting says that better than an effect that
 *  resets four pieces of state. */
export function TransferDrawer({
  vault,
  asset,
  onClose,
}: {
  vault: VaultInfo;
  asset: TransferAsset;
  onClose: () => void;
}) {
  const { connected, signAndSubmit } = useWallet();
  const queryClient = useQueryClient();
  const [direction, setDirection] = useState<Direction>("in");
  const [amount, setAmount] = useState("");
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const move = useMutation({
    mutationFn: async () => {
      if (direction === "out") {
        const result = await api.withdrawFromVault(vault.account, {
          destination: vault.runner_address,
          amount,
          mint: asset.mint,
        });
        return result.signature ?? "";
      }
      const build: VaultBuild = await api.buildVaultAction(vault.account, "deposit", {
        amount,
        ...(asset.mint ? { mint: asset.mint } : {}),
      });
      return signAndSubmit(vault.server, build, vault.network);
    },
    onSuccess: (signature) => {
      setDone(signature);
      setAmount("");
      queryClient.invalidateQueries({ queryKey: ["vault-holdings", vault.server, vault.account] });
    },
  });

  const value = Number(amount);
  const tooMuch = direction === "out" && value > asset.vaultAmount;
  const ready = value > 0 && !tooMuch && !move.isPending && (direction === "out" || !!connected);

  const runnerBox = (
    <Party
      label={direction === "in" ? "From" : "To"}
      name={connected ? "Your wallet" : "The runner's wallet"}
      address={vault.runner_address}
    />
  );
  const vaultBox = (
    <Party
      label={direction === "in" ? "To" : "From"}
      name={vault.label || "This vault"}
      address={vault.wallet_address}
      balance={`${fmt(asset.vaultAmount)} ${asset.symbol}`}
      onMax={direction === "out" ? () => setAmount(String(asset.vaultAmount)) : undefined}
    />
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center sm:p-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-label={`Transfer ${asset.symbol}`}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-t-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 shadow-xl sm:rounded-2xl"
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold">Transfer {asset.symbol}</h2>
            <p className="mt-0.5 text-[12px] text-[var(--color-text-muted)]">
              {direction === "in"
                ? "Funding the vault. Your wallet signs."
                : "Taking it back out. The delegate moves it — no signature needed."}
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

        {direction === "in" ? runnerBox : vaultBox}

        <div className="relative my-2 flex justify-center">
          <button
            type="button"
            onClick={() => {
              setDirection((d) => (d === "in" ? "out" : "in"));
              setDone(null);
            }}
            aria-label="Swap direction"
            title="Swap direction"
            className="rounded-full border border-[var(--color-border)] bg-[var(--color-bg)] p-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            <ArrowUpDown className="h-3.5 w-3.5" />
          </button>
        </div>

        {direction === "in" ? vaultBox : runnerBox}

        <label className="mt-3 block">
          <span className="text-[11px] text-[var(--color-text-muted)]">Amount</span>
          <input
            value={amount}
            onChange={(e) => {
              setAmount(e.target.value);
              setDone(null);
            }}
            inputMode="decimal"
            placeholder="0.00"
            aria-label={`Amount of ${asset.symbol}`}
            className="w-full bg-transparent font-mono text-3xl font-semibold outline-none placeholder:text-[var(--color-text-muted)]/50"
          />
        </label>

        {move.error && (
          <p className="mt-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-[12px] text-[var(--color-red)]">
            {(move.error as Error).message}
          </p>
        )}
        {done && (
          <p className="mt-2 text-[12px] text-[var(--color-green)]">
            Transferred · {done.slice(0, 10)}…
          </p>
        )}

        <button
          type="button"
          disabled={!ready}
          onClick={() => move.mutate()}
          className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-lg bg-[var(--color-accent)] px-3 py-2.5 text-[13px] font-medium text-white disabled:opacity-50"
        >
          {move.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {move.isPending
            ? direction === "in"
              ? "Confirm in your wallet…"
              : "Moving…"
            : tooMuch
              ? `The vault holds ${fmt(asset.vaultAmount)} ${asset.symbol}`
              : direction === "in" && !connected
                ? "Connect a wallet to fund this vault"
                : direction === "in"
                  ? `Deposit ${asset.symbol}`
                  : `Withdraw ${asset.symbol}`}
        </button>
      </div>
    </div>
  );
}

function Party({
  label,
  name,
  address,
  balance,
  onMax,
}: {
  label: string;
  name: string;
  address: string;
  balance?: string;
  onMax?: () => void;
}) {
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2.5">
      <div className="mb-1 flex items-center justify-between text-[11px] text-[var(--color-text-muted)]">
        <span>{label}</span>
        {balance && (
          <span className="flex items-center gap-2 font-mono">
            {balance}
            {onMax && (
              <button
                type="button"
                onClick={onMax}
                className="font-sans font-medium text-[var(--color-accent)] hover:underline"
              >
                MAX
              </button>
            )}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2">
        <AddressAvatar address={address} className="h-6 w-6" />
        <div className="min-w-0">
          <p className="truncate text-[13px] font-medium">{name}</p>
          <p className="font-mono text-[11px] text-[var(--color-text-muted)]">
            {shortAddress(address)}
          </p>
        </div>
      </div>
    </div>
  );
}
