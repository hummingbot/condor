/**
 * The header's wallet control: a Connect button, or the account it is
 * connected as.
 *
 * It sits beside the server selector because it answers the same kind of
 * question — *whose* is this, right now — and it answers only that one. Whether
 * the key this browser holds is also the one this Condor account signs as
 * belongs to the surfaces where it changes an outcome: the vault that would
 * refuse the signature, and Settings beside the wallet it is about. In the
 * header it was a warning on every page about something the page was not doing.
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
  const { available, connected, connect } = useWallet();
  const [open, setOpen] = useState(false);
  const [picking, setPicking] = useState(false);
  // State rather than a ref: the menu positions itself against this element
  // during render, and a ref read there is a value React has not promised is
  // current. The same shape the DEX chain selector uses.
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);

  // The connected key alone. The menu is an account card — what this wallet
  // holds, what it can sign — and none of that is answerable about a key the
  // browser is not holding. Showing the attached address here put a menu
  // behind it that could only explain its own emptiness; not connected is a
  // Connect button, which is the one useful thing in that state.
  const address = connected?.address ?? null;

  return (
    <div className="relative">
      <button
        ref={setAnchor}
        type="button"
        onClick={() => (address ? setOpen((v) => !v) : setPicking(true))}
        title={address ? `This browser holds ${address}` : "Connect a wallet"}
        className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1 text-[11px] font-medium text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
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
        <WalletMenu onDone={() => setOpen(false)} />
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
