/**
 * Whether this browser can sign for this Condor account right now.
 *
 * Three different "no", and they need different sentences: nothing attached
 * (prove a key first), attached but this browser is not holding it (connect),
 * or holding a *different* key (the chain would reject the signature). Every
 * vault surface asks the same question, so it is answered once — and asked
 * before an action is offered, not after it has been built and refused.
 *
 * Its own module because the components that ask are components: a file that
 * exports both loses fast refresh for everything importing it.
 */
import { useWallet } from "@/lib/wallet/context";

export type CannotSign = "none" | "not-connected" | "mismatched";

export function useCanSign(): { canSign: boolean; why: CannotSign | null } {
  const { attached, connected, mismatched } = useWallet();
  if (mismatched) return { canSign: false, why: "mismatched" };
  if (!attached) return { canSign: false, why: "none" };
  if (!connected) return { canSign: false, why: "not-connected" };
  return { canSign: true, why: null };
}
