/**
 * The header's wallet control: a Connect button, or the account it is
 * connected as.
 *
 * It sits beside the server selector because it answers the same kind of
 * question — *whose* is this, right now. A vault's runner is a key rather than
 * a login, so the address here is the one the chain will check, and the control
 * is deliberately loud when the browser's wallet and the attached one disagree.
 *
 * It renders whenever the wallet layer is live — including when this browser
 * has no wallet at all, because that is the case someone most needs told. The
 * picker says which wallets exist and where to get one; a control that hid
 * itself would leave them with a page asking them to connect and nothing to
 * click.
 */
import { ChevronDown, Wallet } from "lucide-react";
import { useState } from "react";

import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import { shortAddress } from "@/components/wallet/address";
import { AddressAvatar } from "@/components/wallet/primitives";
import { WalletMenu } from "@/components/wallet/WalletMenu";
import { WalletPicker } from "@/components/wallet/WalletPicker";
import { useWallet } from "@/lib/wallet/context";

export function WalletControl() {
  const { available, connected, attached, mismatched, connect } = useWallet();
  const [open, setOpen] = useState(false);
  const [picking, setPicking] = useState(false);
  // State rather than a ref: the menu positions itself against this element
  // during render, and a ref read there is a value React has not promised is
  // current. The same shape the DEX chain selector uses.
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);

  const address = connected?.address ?? attached;

  return (
    <div className="relative">
      <button
        ref={setAnchor}
        type="button"
        onClick={() => (address ? setOpen((v) => !v) : setPicking(true))}
        title={
          mismatched
            ? "This browser's wallet is not the one attached to this account"
            : address
              ? `Signing as ${address}`
              : "Connect a wallet"
        }
        className={`inline-flex items-center gap-1.5 rounded-md border bg-[var(--color-bg)] px-2 py-1 text-[11px] font-medium transition-colors hover:text-[var(--color-text)] ${
          mismatched
            ? "border-amber-500/50 text-amber-600 dark:text-amber-400"
            : "border-[var(--color-border)] text-[var(--color-text-muted)]"
        }`}
      >
        {address ? (
          <>
            <AddressAvatar address={address} className="h-4 w-4" />
            <span className="font-mono">{shortAddress(address)}</span>
            <ChevronDown className="h-3 w-3 opacity-60" />
          </>
        ) : (
          <>
            <Wallet className="h-3.5 w-3.5" />
            Connect
          </>
        )}
      </button>

      <AnchoredMenu
        anchor={anchor}
        open={open && !!address}
        onClose={() => setOpen(false)}
        align="right"
        className="w-auto"
      >
        <WalletMenu
          onDone={() => setOpen(false)}
          onConnectRequest={() => {
            setOpen(false);
            setPicking(true);
          }}
        />
      </AnchoredMenu>

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
