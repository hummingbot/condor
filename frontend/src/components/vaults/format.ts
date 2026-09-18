/**
 * The vault facts every surface needs, with no components attached.
 *
 * Its own module because `shared.tsx` renders things, and a file that exports
 * both a component and a helper loses fast refresh for everything that imports
 * it (`react-refresh/only-export-components`).
 */
import type { VaultInfo } from "@/lib/api";

/**
 * Whether anyone but the runner has a claim on what is inside.
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
