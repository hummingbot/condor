/**
 * The connected wallet, and the one path from a build to a confirmed signature.
 *
 * Discovery, connecting and signing are **ConnectorKit**'s (`@solana/connector`):
 * it speaks the Wallet Standard, keeps the session, and hands back a signer.
 * What lives here is the part ConnectorKit has no opinion about, because it is
 * Condor's:
 *
 * 1. **Which key is this person's.** A wallet connects in the browser and then
 *    *attaches* to the Condor account with one signature over a one-time
 *    message. Connecting is a browser fact; attaching is what makes the key this
 *    user's creator identity, and every vault route checks the second.
 * 2. **Signing and submitting.** `signAndSubmit` takes what Gateway built, hands
 *    the bytes to the wallet, posts them back to Condor, and waits for the
 *    chain. The *submission* never goes direct: Condor submits through Gateway's
 *    own node, which is the node the transaction was built and simulated
 *    against.
 *
 * The cluster ConnectorKit reads comes from the server's own Gateway, so the
 * chain the wallet UI describes is the chain a signature would land on. Nothing
 * mounts until that answer arrives — a wallet layer pointed at the wrong chain
 * is worse than one that is not there yet.
 *
 * Dev keypairs register themselves as Wallet Standard wallets, but only once
 * the chain endpoint has said `surfpool` — see `devRegister.ts` for why that
 * gate is an answer from the node rather than a build flag.
 */
import {
  AppProvider,
  getDefaultConfig,
  useConnectWallet,
  useConnector,
  useDisconnectWallet,
  useTransactionSigner,
  useWalletConnectors,
  type WalletConnectorId,
} from "@solana/connector/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, type ReactNode } from "react";

import { api, type VaultBuild } from "@/lib/api";
import { WALLET_NAME_KEY } from "@/lib/sessionState";

import { WalletContext, type WalletState } from "./context";
import { registerDevWallets } from "./devRegister";

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
  // Which chain this server's Gateway is on, and the RPC that answers for it.
  // The wallet layer reads that node directly for balances and history; every
  // transaction still goes back through Condor to Gateway.
  const chain = useQuery({
    queryKey: ["gateway-chain", server],
    queryFn: () => api.getGatewayChain(server!),
    enabled: !!server,
    retry: false,
    staleTime: 60_000,
  });
  const rpcUrl = chain.data?.rpc_url ?? null;
  const isFork = chain.data?.kind === "surfpool";

  useEffect(() => {
    void registerDevWallets(isFork);
  }, [isFork]);

  const config = useMemo(() => {
    if (!rpcUrl) return null;
    return getDefaultConfig({
      appName: "Condor",
      // Reconnection is handled below, once, against the wallet this browser
      // last used. The library's own autoConnect races the provider teardown
      // React runs in development.
      autoConnect: false,
      // One cluster, always. Offering devnet would only let someone switch to a
      // chain this server's Gateway is not on — and the fork presents itself as
      // mainnet, which is what wallets sign for and what Gateway submits to.
      clusters: [
        {
          id: "solana:mainnet",
          label: isFork ? "Mainnet fork" : "Mainnet",
          url: rpcUrl,
        },
      ],
    });
  }, [rpcUrl, isFork]);

  // No cluster, no wallet layer: a picker that cannot say which chain it would
  // sign for is a trap, and every page already handles "no wallet yet".
  if (!config) {
    return <WalletContext value={OFFLINE}>{children}</WalletContext>;
  }

  return (
    <AppProvider key={rpcUrl} connectorConfig={config}>
      <ConnectorBridge>{children}</ConnectorBridge>
    </AppProvider>
  );
}

/** What every page sees before the server has said which chain it is on. */
const OFFLINE: WalletState = {
  available: [],
  connected: null,
  attached: null,
  mismatched: false,
  connect: async () => {
    throw new Error("the wallet layer is still reading this server's chain");
  },
  disconnect: () => {},
  attach: async () => {
    throw new Error("the wallet layer is still reading this server's chain");
  },
  detach: async () => {},
  signAndSubmit: async () => {
    throw new Error("the wallet layer is still reading this server's chain");
  },
  signAndSubmitAll: async () => {
    throw new Error("the wallet layer is still reading this server's chain");
  },
};

/**
 * ConnectorKit's session, published as Condor's wallet state.
 *
 * Inside `AppProvider` because that is where its hooks work, and separate from
 * the provider above because that one decides *which chain* — a decision made
 * before a connector exists.
 */
function ConnectorBridge({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const { isConnected, connector } = useConnector();
  const { signer, address } = useTransactionSigner();
  const { connect: connectWallet } = useConnectWallet();
  const { disconnect: disconnectWallet } = useDisconnectWallet();
  const connectors = useWalletConnectors();

  const attached = useQuery({
    queryKey: ["wallet"],
    queryFn: () => api.getWallet(),
    retry: false,
  });

  const available = useMemo(
    () => connectors.map((entry) => ({ id: entry.id, name: entry.name, icon: entry.icon })),
    [connectors],
  );

  // One silent reconnect on load, for the wallet this browser last used.
  // Silent because a prompt on every page load teaches people to click through
  // prompts; once because a failed attempt should not retry on every render.
  //
  // A ref rather than state: "have we tried yet" is bookkeeping, not something
  // the render reads, and setting state inside the effect that reads it is how
  // an effect becomes a render loop.
  const tried = useRef(false);
  useEffect(() => {
    if (tried.current || isConnected || connectors.length === 0) return;
    tried.current = true;
    let remembered: string | null = null;
    try {
      remembered = localStorage.getItem(WALLET_NAME_KEY);
    } catch {
      // Private windows and blocked site data throw rather than return null.
      return;
    }
    if (!remembered) return;
    if (!connectors.some((entry) => entry.id === remembered)) return;
    void connectWallet(remembered as WalletConnectorId, { silent: true }).catch(() => {});
  }, [isConnected, connectors, connectWallet]);

  const connect = useCallback(
    async (id: string) => {
      await connectWallet(id as WalletConnectorId);
      try {
        localStorage.setItem(WALLET_NAME_KEY, id);
      } catch {
        // A browser that refuses storage still connects; it just re-asks next
        // load, which is the honest outcome rather than a failed connect.
      }
    },
    [connectWallet],
  );

  const disconnect = useCallback(() => {
    try {
      localStorage.removeItem(WALLET_NAME_KEY);
    } catch {
      // Nothing to forget if it was never stored.
    }
    void disconnectWallet();
  }, [disconnectWallet]);

  const attach = useCallback(async () => {
    if (!signer || !address) throw new Error("connect a wallet first");
    if (!signer.signMessage) {
      throw new Error(`${connector?.name ?? "this wallet"} cannot sign messages`);
    }
    // The server composes the message and the browser signs it verbatim.
    // Building it on both sides would be two implementations of one string.
    const { nonce, message } = await api.walletNonce(address);
    const signature = await signer.signMessage(new TextEncoder().encode(message));
    await api.attachWallet({ address, signature: base64(signature), nonce });
    await queryClient.invalidateQueries({ queryKey: ["wallet"] });
  }, [signer, address, connector?.name, queryClient]);

  const detach = useCallback(async () => {
    await api.detachWallet();
    await queryClient.invalidateQueries({ queryKey: ["wallet"] });
  }, [queryClient]);

  const signAndSubmit = useCallback(
    async (targetServer: string, build: VaultBuild, network = "mainnet-beta") => {
      if (!signer) throw new Error("connect a wallet first");
      const signed = await signer.signTransaction(fromBase64(build.transaction));
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
    [signer],
  );

  const signAndSubmitAll = useCallback(
    async (targetServer: string, builds: VaultBuild[], network = "mainnet-beta") => {
      const signatures: string[] = [];
      // Sequential on purpose: each step of a flow reads the account the
      // previous one wrote, so they cannot be in flight together.
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
      connected:
        isConnected && address
          ? { id: connector?.id ?? "", name: connector?.name ?? "wallet", icon: connector?.icon ?? "", address }
          : null,
      attached: attached.data?.address ?? null,
      mismatched:
        !!address && !!attached.data?.address && address !== attached.data.address,
      connect,
      disconnect,
      attach,
      detach,
      signAndSubmit,
      signAndSubmitAll,
    }),
    [
      available,
      isConnected,
      address,
      connector?.id,
      connector?.name,
      connector?.icon,
      attached.data,
      connect,
      disconnect,
      attach,
      detach,
      signAndSubmit,
      signAndSubmitAll,
    ],
  );

  return <WalletContext value={value}>{children}</WalletContext>;
}
