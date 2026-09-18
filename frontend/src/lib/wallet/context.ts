/**
 * The wallet context and its hook, split from the provider that fills it.
 *
 * Its own module so `WalletProvider.tsx` exports a component and nothing else:
 * a file that exports both loses fast refresh for everything importing it.
 */
import { createContext, use } from "react";

import type { VaultBuild } from "@/lib/api";

import type { AvailableWallet, ConnectedWallet } from "./standard";

export interface WalletState {
  /** Installed wallets, plus dev keypairs on a fork. */
  available: AvailableWallet[];
  /** The wallet this browser is connected to, if any. */
  connected: ConnectedWallet | null;
  /** The address this Condor account has proved it controls, if any. */
  attached: string | null;
  /**
   * The browser is connected to a different key than the one attached.
   *
   * Worth its own flag rather than left to each caller to compare: signing with
   * the wrong account produces a transaction the program rejects for a reason
   * that is invisible from the outside, so every surface has to say so before
   * the signature rather than after it.
   */
  mismatched: boolean;
  connect(name: string): Promise<void>;
  disconnect(): void;
  attach(): Promise<void>;
  detach(): Promise<void>;
  signAndSubmit(server: string, build: VaultBuild, network?: string): Promise<string>;
  signAndSubmitAll(server: string, builds: VaultBuild[], network?: string): Promise<string[]>;
}

export const WalletContext = createContext<WalletState | null>(null);

export function useWallet(): WalletState {
  const state = use(WalletContext);
  if (!state) throw new Error("useWallet must be used inside a WalletProvider");
  return state;
}
