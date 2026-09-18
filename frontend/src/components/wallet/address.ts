/**
 * Address arithmetic for the wallet UI: the short form, and what this browser
 * last connected to.
 *
 * Its own module so the components beside it export components and nothing
 * else — a file that exports both loses fast refresh for everything importing
 * it, and these are imported from the header, the picker and the dev wallets.
 *
 * The face an address wears lives in `lib/avatarStyle`, because it is a choice
 * someone makes rather than arithmetic on the string.
 */
import { WALLET_NAME_KEY } from "@/lib/sessionState";

export const shortAddress = (address: string) =>
  `${address.slice(0, 4)}…${address.slice(-4)}`;

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
