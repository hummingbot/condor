/**
 * The wallet context and its hook, split from the provider that fills it.
 *
 * Its own module so `WalletProvider.tsx` exports a component and nothing else:
 * a file that exports both loses fast refresh for everything importing it.
 */
import { createContext, use } from "react";

import type { VaultBuild } from "@/lib/api";

/** A wallet the picker can offer. `id` is what `connect` takes — ConnectorKit
 *  keys its session on a stable connector id, not on a display name. */
export interface AvailableWallet {
  id: string;
  name: string;
  icon: string;
}

/** The wallet this browser is connected to. */
export interface ConnectedWallet {
  id: string;
  name: string;
  icon: string;
  address: string;
}

export interface WalletState {
  /** Wallets this browser can offer, dev keypairs among them on a fork. */
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
  connect(id: string): Promise<void>;
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
