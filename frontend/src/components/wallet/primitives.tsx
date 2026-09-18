/**
 * The small pieces the wallet UI needs and nothing else has.
 *
 * An address gets a face here rather than a monospace string alone, because
 * recognising the right key at a glance is the whole job of this control: four
 * leading characters are easy to misread and a shape is not.
 */
import { Wallet } from "lucide-react";
import { useMemo, useState } from "react";

import { avatarSvg } from "./address";

/** A wallet's own icon, with a fallback when the extension's URL fails. */
export function WalletAvatar({
  src,
  alt,
  className = "h-5 w-5",
}: {
  src?: string;
  alt?: string;
  className?: string;
}) {
  const [broken, setBroken] = useState(false);
  if (src && !broken) {
    return (
      <img
        src={src}
        alt={alt ?? ""}
        onError={() => setBroken(true)}
        className={`shrink-0 rounded-full object-cover ${className}`}
      />
    );
  }
  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center rounded-full bg-[var(--color-surface-hover)] text-[var(--color-text-muted)] ${className}`}
    >
      <Wallet className="h-1/2 w-1/2" />
    </span>
  );
}

/**
 * A deterministic face for an address: concentric rings whose hues come from
 * the address itself, so the same key is the same shape everywhere it appears
 * and two similar-looking addresses are not similar-looking faces.
 *
 * Drawn here rather than fetched from an avatar service: it is nine lines of
 * arithmetic, it works offline, and a wallet identity should not be a request
 * to somebody else's server.
 */
export function AddressAvatar({
  address,
  className = "h-5 w-5",
}: {
  address: string;
  className?: string;
}) {
  const svg = useMemo(() => avatarSvg(address), [address]);
  return (
    <span
      aria-hidden
      className={`inline-block shrink-0 overflow-hidden rounded-full [&>svg]:h-full [&>svg]:w-full ${className}`}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}

export function Spinner({ className = "h-3.5 w-3.5" }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="Working"
      className={`inline-block animate-spin rounded-full border-2 border-current border-t-transparent ${className}`}
    />
  );
}
