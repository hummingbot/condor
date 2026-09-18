/**
 * The vault facts every surface needs, with no components attached.
 *
 * Its own module because `shared.tsx` renders things, and a file that exports
 * both a component and a helper loses fast refresh for everything that imports
 * it (`react-refresh/only-export-components`).
 */
import type { VaultInfo } from "@/lib/api";

/**
 * Whether anyone but the creator has a claim on what is inside.
 *
 * The single most consequential fact about a vault — it decides whether a
 * withdrawal is routine or impossible — so it is computed in one place. The
 * chain is the arbiter; the local record is only a hint, and is consulted just
 * for a draft whose account has not been read yet.
 */
export function isTokenized(vault: VaultInfo): boolean {
  return vault.chain ? vault.chain.tokenized : !!vault.token?.mint;
}

export function shortAddress(address: string, size = 4): string {
  return address.length <= size * 2 + 1
    ? address
    : `${address.slice(0, size)}…${address.slice(-size)}`;
}

/** Basis points as a percentage, trimming a trailing `.0`. */
export function pctOfBps(bps: number): string {
  return `${(bps / 100).toFixed(bps % 100 === 0 ? 0 : 1)}%`;
}

/**
 * What share of a vault token's supply the curve offered, from the two figures
 * the program records.
 *
 * Two numbers rather than a stored percentage because neither is an SPL or
 * Token-2022 field — a mint carries only its live `supply`, which redemption
 * burns down — so the ratio is computed where it is shown and the tokens stay
 * comparable with every other balance. BigInt throughout: these are base units
 * of a fixed supply, and at 1e15 a float has already stopped being exact.
 */
export function supplyShare(circulating?: string | null, total?: string | null): string {
  if (!circulating || !total) return "—";
  try {
    const c = BigInt(circulating);
    const t = BigInt(total);
    if (t === 0n) return "—";
    const tenths = Number((c * 1000n) / t) / 10;
    return `${tenths.toFixed(tenths % 1 === 0 ? 0 : 1)}%`;
  } catch {
    return "—";
  }
}
