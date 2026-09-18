/**
 * "Connect wallet", wherever connecting is the next thing to do.
 *
 * One component rather than a picker raised from four places: every surface
 * that asks someone to connect should offer the same list, including the case
 * where the list is empty and the honest answer is "install one" — which the
 * picker says and a bare disabled button does not.
 */
import { Wallet } from "lucide-react";
import { useState } from "react";

import { useWallet } from "@/lib/wallet/context";

import { WalletPicker } from "./WalletPicker";

export function ConnectWalletButton({
  label = "Connect wallet",
  variant = "accent",
  className = "",
}: {
  label?: string;
  variant?: "accent" | "outline";
  className?: string;
}) {
  const { available, connect } = useWallet();
  const [picking, setPicking] = useState(false);

  const tone =
    variant === "accent"
      ? "bg-[var(--color-accent)] text-white"
      : "border border-[var(--color-border)] hover:bg-[var(--color-surface-hover)]";

  return (
    <>
      <button
        type="button"
        onClick={() => setPicking(true)}
        className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-medium ${tone} ${className}`}
      >
        <Wallet className="h-3.5 w-3.5" />
        {label}
      </button>
      {picking && (
        <WalletPicker
          available={available}
          onConnect={connect}
          onClose={() => setPicking(false)}
        />
      )}
    </>
  );
}
