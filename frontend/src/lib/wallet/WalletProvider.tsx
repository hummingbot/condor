/**
 * The connected wallet, and the one path from a build to a confirmed signature.
 *
 * Two things live here because they are the same thing seen twice:
 *
 * 1. **Which key is this person's.** A wallet connects in the browser and then
 *    *attaches* to the Condor account with one signature over a one-time
 *    message. Connecting is a browser fact; attaching is what makes the key this
 *    user's runner identity, and every vault route checks the second.
 * 2. **Signing and submitting.** `signAndSubmit` takes what Gateway built,
 *    hands the bytes to the wallet, posts them back to Condor, and waits for
 *    the chain. The browser never holds an RPC URL: Condor submits through
 *    Gateway's own node, which is the node the transaction was built and
 *    simulated against.
 *
 * Dev keypairs appear beside real wallets, but only once the chain endpoint has
 * said `surfpool` — see `dev.ts` for why that gate is an answer from the node
 * rather than a build flag.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, type VaultBuild } from "@/lib/api";
import { WALLET_NAME_KEY } from "@/lib/sessionState";

import { WalletContext, type WalletState } from "./context";
import { devWallets } from "./dev";
import {
  availableWallets,
  onWalletsChanged,
  reconnect,
  type AvailableWallet,
  type ConnectedWallet,
} from "./standard";

function base64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function fromBase64(value: string): Uint8Array {
  const binary = atob(value);
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}

export function WalletProvider({
  server,
  children,
}: {
  server: string | null;
  children: ReactNode;
}) {
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState<ConnectedWallet | null>(null);
  const [installed, setInstalled] = useState<AvailableWallet[]>(() => availableWallets());

  // A wallet can be installed while the page is open; the list is not a
  // one-time read.
  useEffect(() => onWalletsChanged(() => setInstalled(availableWallets())), []);

  const chain = useQuery({
    queryKey: ["gateway-chain", server],
    queryFn: () => api.getGatewayChain(server!),
    enabled: !!server,
    retry: false,
    staleTime: 30_000,
  });
  const isFork = chain.data?.kind === "surfpool";

  const attached = useQuery({
    queryKey: ["wallet"],
    queryFn: () => api.getWallet(),
    retry: false,
  });

  const dev = useMemo(() => devWallets(isFork), [isFork]);
  const available = useMemo<AvailableWallet[]>(
    () => [
      ...installed,
      ...dev.map((wallet) => ({
        name: wallet.name,
        icon: wallet.icon,
        connect: async () => wallet,
      })),
    ],
    [installed, dev],
  );

  // The wallet this browser last used, read once. A dev keypair needs no
  // handshake at all, so it resolves here rather than through the effect below
  // — deriving it beats setting state during one.
  const [remembered] = useState(() => {
    try {
      return localStorage.getItem(WALLET_NAME_KEY);
    } catch {
      // Private windows and blocked site data throw rather than return null.
      return null;
    }
  });
  const rememberedDev = useMemo(
    () => (remembered ? (dev.find((w) => w.name === remembered) ?? null) : null),
    [remembered, dev],
  );

  // One silent reconnect on load, for a real wallet: it is a handshake with
  // something outside React, which is what an effect is for. Silent because a
  // reconnect that prompts on every page load teaches people to click through
  // prompts.
  useEffect(() => {
    if (connected || rememberedDev || !remembered) return;
    let cancelled = false;
    reconnect(remembered).then((wallet) => {
      if (!cancelled && wallet) setConnected(wallet);
    });
    return () => {
      cancelled = true;
    };
  }, [connected, remembered, rememberedDev]);

  const active = connected ?? rememberedDev;

  const connect = useCallback(
    async (name: string) => {
      const entry = available.find((w) => w.name === name);
      if (!entry) throw new Error(`${name} is not available`);
      const wallet = await entry.connect();
      localStorage.setItem(WALLET_NAME_KEY, name);
      setConnected(wallet);
    },
    [available],
  );

  const disconnect = useCallback(() => {
    localStorage.removeItem(WALLET_NAME_KEY);
    setConnected(null);
  }, []);

  const attach = useCallback(async () => {
    if (!active) throw new Error("connect a wallet first");
    // The server composes the message and the browser signs it verbatim.
    // Building it on both sides would be two implementations of one string.
    const { nonce, message } = await api.walletNonce(active.address);
    const signature = await active.signMessage(new TextEncoder().encode(message));
    await api.attachWallet({
      address: active.address,
      signature: base64(signature),
      nonce,
    });
    await queryClient.invalidateQueries({ queryKey: ["wallet"] });
  }, [active, queryClient]);

  const detach = useCallback(async () => {
    await api.detachWallet();
    await queryClient.invalidateQueries({ queryKey: ["wallet"] });
  }, [queryClient]);

  const signAndSubmit = useCallback(
    async (targetServer: string, build: VaultBuild, network = "mainnet-beta") => {
      if (!active) throw new Error("connect a wallet first");
      const signed = await active.signTransaction(fromBase64(build.transaction));
      const { signature } = await api.submitTransaction(targetServer, {
        network,
        signed_transaction: base64(signed),
        // Handed back so Gateway can re-merge them: some wallets re-serialize
        // with only their own signature, which would drop the mint keypair's or
        // the administrator's and fail verification before broadcast.
        extra_signers: build.extra_signers,
      });
      return signature;
    },
    [active],
  );

  const signAndSubmitAll = useCallback(
    async (targetServer: string, builds: VaultBuild[], network = "mainnet-beta") => {
      const signatures: string[] = [];
      // Sequential on purpose: each step of the create flow reads the account
      // the previous one wrote, so they cannot be in flight together.
      for (const build of builds) {
        signatures.push(await signAndSubmit(targetServer, build, network));
      }
      return signatures;
    },
    [signAndSubmit],
  );

  const value = useMemo<WalletState>(
    () => ({
      available,
      connected: active,
      attached: attached.data?.address ?? null,
      mismatched:
        !!active && !!attached.data?.address && active.address !== attached.data.address,
      connect,
      disconnect,
      attach,
      detach,
      signAndSubmit,
      signAndSubmitAll,
    }),
    [available, active, attached.data, connect, disconnect, attach, detach, signAndSubmit, signAndSubmitAll],
  );

  return <WalletContext value={value}>{children}</WalletContext>;
}
