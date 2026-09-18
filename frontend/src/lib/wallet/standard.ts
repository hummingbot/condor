/**
 * Browser wallets, through the Wallet Standard.
 *
 * Condor holds no user key (plan D2): a runner's signature comes from their own
 * wallet, in their own browser, over a message Gateway built. This file is the
 * whole of that — discovery, connect, sign — and it deliberately talks to the
 * Wallet Standard rather than to any wallet's own injected object, so Phantom
 * is a target rather than a dependency.
 *
 * Nothing here holds an RPC URL. The browser signs bytes and hands them back to
 * Condor, which submits through Gateway's node — the same node the transaction
 * was built and simulated against. Two answers about one chain is how the wrong
 * one wins (plan §6).
 */
import { getWallets } from "@wallet-standard/app";

/** The three features a wallet needs before Condor will offer it. */
const CONNECT = "standard:connect";
const SIGN_TRANSACTION = "solana:signTransaction";
const SIGN_MESSAGE = "solana:signMessage";

/** Solana mainnet's Wallet Standard chain identifier. */
export const SOLANA_MAINNET = "solana:mainnet";

export interface WalletAccount {
  address: string;
  publicKey: Uint8Array;
  chains: readonly string[];
  features: readonly string[];
}

interface StandardWallet {
  name: string;
  icon: string;
  chains: readonly string[];
  accounts: readonly WalletAccount[];
  features: Record<string, unknown>;
}

export interface ConnectedWallet {
  name: string;
  icon: string;
  address: string;
  signTransaction(transaction: Uint8Array): Promise<Uint8Array>;
  signMessage(message: Uint8Array): Promise<Uint8Array>;
}

export interface AvailableWallet {
  name: string;
  icon: string;
  connect(): Promise<ConnectedWallet>;
}

function supportsSolana(wallet: StandardWallet): boolean {
  const features = wallet.features ?? {};
  return (
    CONNECT in features &&
    SIGN_TRANSACTION in features &&
    SIGN_MESSAGE in features &&
    wallet.chains.some((chain) => chain.startsWith("solana:"))
  );
}

/**
 * Wrap a connected account's features as plain functions.
 *
 * The Wallet Standard's signing calls take and return arrays of inputs and
 * outputs; every call Condor makes is for exactly one transaction, so the
 * shape is flattened here rather than at eight call sites.
 */
function bind(wallet: StandardWallet, account: WalletAccount): ConnectedWallet {
  const signTx = wallet.features[SIGN_TRANSACTION] as {
    signTransaction(...inputs: unknown[]): Promise<{ signedTransaction: Uint8Array }[]>;
  };
  const signMsg = wallet.features[SIGN_MESSAGE] as {
    signMessage(...inputs: unknown[]): Promise<{ signature: Uint8Array }[]>;
  };
  return {
    name: wallet.name,
    icon: wallet.icon,
    address: account.address,
    async signTransaction(transaction) {
      const [result] = await signTx.signTransaction({ account, transaction });
      if (!result?.signedTransaction) {
        throw new Error(`${wallet.name} returned no signed transaction`);
      }
      return result.signedTransaction;
    },
    async signMessage(message) {
      const [result] = await signMsg.signMessage({ account, message });
      if (!result?.signature) throw new Error(`${wallet.name} returned no signature`);
      return result.signature;
    },
  };
}

/** Every installed wallet that can sign a Solana transaction and a message. */
export function availableWallets(): AvailableWallet[] {
  const { get } = getWallets();
  return (get() as unknown as StandardWallet[]).filter(supportsSolana).map((wallet) => ({
    name: wallet.name,
    icon: wallet.icon,
    async connect() {
      const feature = wallet.features[CONNECT] as {
        connect(input?: { silent?: boolean }): Promise<{ accounts: readonly WalletAccount[] }>;
      };
      const { accounts } = await feature.connect();
      const account = accounts[0];
      if (!account) throw new Error(`${wallet.name} connected with no account`);
      return bind(wallet, account);
    },
  }));
}

/**
 * Reconnect to a wallet the user has already approved, without a prompt.
 *
 * `silent: true` is the whole point: a reconnect that opens a dialog on every
 * page load trains people to click through dialogs. A wallet that does not
 * support it, or that has forgotten this site, simply returns nothing and the
 * user connects by hand.
 */
export async function reconnect(name: string): Promise<ConnectedWallet | null> {
  const { get } = getWallets();
  const wallet = (get() as unknown as StandardWallet[]).find(
    (w) => w.name === name && supportsSolana(w),
  );
  if (!wallet) return null;
  const feature = wallet.features[CONNECT] as {
    connect(input?: { silent?: boolean }): Promise<{ accounts: readonly WalletAccount[] }>;
  };
  try {
    const { accounts } = await feature.connect({ silent: true });
    const account = accounts[0];
    return account ? bind(wallet, account) : null;
  } catch {
    // A wallet that refuses a silent connect is not an error; it is a wallet
    // that wants to be asked.
    return null;
  }
}

/** Notifies when a wallet is installed or removed while the page is open. */
export function onWalletsChanged(listener: () => void): () => void {
  const { on } = getWallets();
  const offRegister = on("register", listener);
  const offUnregister = on("unregister", listener);
  return () => {
    offRegister();
    offUnregister();
  };
}
