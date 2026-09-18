/**
 * Address arithmetic for the wallet UI: the short form, and the face.
 *
 * Its own module so the components beside it export components and nothing
 * else — a file that exports both loses fast refresh for everything importing
 * it, and these are imported from the header, the picker and the dev wallets.
 */
import { WALLET_NAME_KEY } from "@/lib/sessionState";

export const shortAddress = (address: string) =>
  `${address.slice(0, 4)}…${address.slice(-4)}`;

function hash(value: string): number {
  // FNV-1a: short, stable across browsers, and good enough to spread hues.
  let h = 0x811c9dc5;
  for (let i = 0; i < value.length; i++) {
    h ^= value.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
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
export function avatarSvg(address: string): string {
  const h = hash(address);
  const hue = h % 360;
  const second = (hue + 40 + ((h >> 8) % 120)) % 360;
  const ring = 30 + ((h >> 16) % 20);
  return [
    '<svg viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">',
    `<circle cx="16" cy="16" r="16" fill="hsl(${hue} 70% 62%)"/>`,
    `<circle cx="16" cy="16" r="10" fill="none" stroke="hsl(${second} 75% ${ring}%)" stroke-width="5"/>`,
    `<circle cx="16" cy="16" r="3.5" fill="hsl(${second} 80% 92%)"/>`,
    "</svg>",
  ].join("");
}

/** Which connector this browser used last, so the picker can say "Recent".
 *  The key itself lives in `sessionState`, which is the one place storage keys
 *  are named — a second spelling of it is a key that survives a logout. */
export function lastConnected(): string | null {
  try {
    return localStorage.getItem(WALLET_NAME_KEY);
  } catch {
    return null;
  }
}
