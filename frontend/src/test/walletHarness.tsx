/**
 * A wallet context for tests of components that merely *sit beside* the wallet.
 *
 * `useWallet` throws without a provider, on purpose: a page that reads the
 * connected key and silently gets nulls is a page that will sign with the wrong
 * one. That rule is right in the app and inconvenient in a test whose subject
 * is something else entirely — the Keys tab's request count, say — so those get
 * this: a wallet layer that is present and holding nothing, which is the state
 * most of the app is in most of the time anyway.
 *
 * Deliberately not a mock of connecting or signing. A test that wants those
 * should say what it expects them to do.
 */
import type { ReactNode } from "react";

import { WalletContext, type WalletState } from "@/lib/wallet/context";

const unavailable = (what: string) => async () => {
  throw new Error(`${what} is not available in this test's wallet harness`);
};

const IDLE_WALLET: WalletState = {
  available: [],
  connected: null,
  attached: null,
  mismatched: false,
  connect: unavailable("connect"),
  disconnect: () => {},
  attach: unavailable("attach"),
  detach: unavailable("detach"),
  signAndSubmit: unavailable("signAndSubmit"),
  signAndSubmitAll: unavailable("signAndSubmitAll"),
};

export function WalletHarness({
  children,
  value = IDLE_WALLET,
}: {
  children: ReactNode;
  value?: WalletState;
}) {
  return <WalletContext value={value}>{children}</WalletContext>;
}
