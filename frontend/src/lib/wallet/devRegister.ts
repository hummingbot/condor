/**
 * Throwaway keypairs, registered as real wallets.
 *
 * Developing the vault flows against a browser extension means approving a
 * dialog per signature, per attempt. A dev keypair skips that — and is exactly
 * the thing that must never touch mainnet, because its secret is in an env var
 * that ends up in the bundle.
 *
 * So the gate is not a convention or a warning: nothing is registered unless
 * the chain endpoint has *said* `surfpool` (plan M2). The check is the
 * backend's `/settings/gateway/chain`, which asks Gateway which RPC it uses and
 * asks that RPC what it is — so "am I on a fork?" is answered by the node the
 * transaction would actually land on, not by a build flag someone forgot.
 *
 * They are registered through the **Wallet Standard**, not bolted onto the app's
 * wallet list, so the connector discovers them exactly as it discovers Phantom
 * and every flow runs one code path: signing here exercises the same wire
 * format a real wallet would.
 */
import {
  createKeyPairFromBytes,
  getAddressFromPublicKey,
  getBase58Encoder,
  getTransactionDecoder,
  getTransactionEncoder,
  partiallySignTransaction,
  signBytes,
} from "@solana/kit";
import {
  SolanaSignMessage,
  SolanaSignTransaction,
  type SolanaSignMessageInput,
  type SolanaSignMessageOutput,
  type SolanaSignTransactionInput,
  type SolanaSignTransactionOutput,
} from "@solana/wallet-standard-features";
import type { IdentifierArray, Wallet, WalletAccount, WalletIcon } from "@wallet-standard/base";
import {
  StandardConnect,
  StandardDisconnect,
  StandardEvents,
  type StandardEventsListeners,
  type StandardEventsOnMethod,
} from "@wallet-standard/features";
import { registerWallet } from "@wallet-standard/wallet";

import { avatarSvg, getAvatarStyleId, loadAvatarStyle } from "@/lib/avatarStyle";

/** The fork presents itself as mainnet — that is what it forked — so a dev
 *  wallet advertises the chain the transaction will actually be signed for. */
const CHAINS: IdentifierArray = ["solana:mainnet", "solana:localnet"];
const ACCOUNT_FEATURES: IdentifierArray = [SolanaSignTransaction, SolanaSignMessage];

type CryptoKeyPair = Awaited<ReturnType<typeof createKeyPairFromBytes>>;

/** `VITE_DEV_WALLET_SECRET_<NAME>=<base58 secret key>`. Two of them make a
 *  two-holder redemption testable without two browsers. */
function devSecrets(): { name: string; secret: string }[] {
  const prefix = "VITE_DEV_WALLET_SECRET_";
  return Object.entries(import.meta.env)
    .filter(([key, value]) => key.startsWith(prefix) && typeof value === "string" && value)
    .map(([key, value]) => ({ name: key.slice(prefix.length).toLowerCase(), secret: value as string }));
}

/** A Wallet Standard icon is a fixed string handed over once at registration,
 *  not something re-read at render, so the avatar style has to be in memory by
 *  then — `registerDevWallets` awaits it. Reaching here without it means that
 *  load failed, which is worth saying rather than papering over with a
 *  different-looking placeholder that then never updates. */
function icon(address: string): WalletIcon {
  const svg = avatarSvg(address);
  if (!svg) throw new Error("avatar style is not loaded; cannot build a dev wallet icon");
  // UTF-8 bytes, not the string: several DiceBear styles carry non-Latin-1
  // characters in their metadata (curly quotes, mostly), and bare `btoa`
  // throws on those. It threw during registration, so *no* dev wallet
  // appeared and the picker looked like the feature was simply off.
  const bytes = new TextEncoder().encode(svg);
  const binary = Array.from(bytes, (byte) => String.fromCharCode(byte)).join("");
  return `data:image/svg+xml;base64,${btoa(binary)}` as WalletIcon;
}

class DevAccount implements WalletAccount {
  readonly chains = CHAINS;
  readonly features = ACCOUNT_FEATURES;
  readonly address: string;
  readonly publicKey: Uint8Array;
  readonly label: string;
  readonly icon: WalletIcon;

  constructor(address: string, publicKey: Uint8Array, label: string) {
    this.address = address;
    this.publicKey = publicKey;
    this.label = label;
    this.icon = icon(address);
  }
}

class DevWallet implements Wallet {
  readonly version = "1.0.0" as const;
  readonly chains = CHAINS;
  readonly name: string;
  readonly icon: WalletIcon;

  #keyPair: CryptoKeyPair;
  #account: DevAccount;
  #connected: DevAccount | null = null;
  #listeners: StandardEventsListeners["change"][] = [];

  constructor(name: string, keyPair: CryptoKeyPair, address: string, publicKey: Uint8Array) {
    this.name = name;
    this.#keyPair = keyPair;
    this.#account = new DevAccount(address, publicKey, name);
    this.icon = icon(address);
  }

  get accounts(): readonly WalletAccount[] {
    return this.#connected ? [this.#connected] : [];
  }

  #emit() {
    for (const listener of this.#listeners) listener({ accounts: this.accounts });
  }

  #on: StandardEventsOnMethod = (event, listener) => {
    if (event !== "change") return () => {};
    this.#listeners.push(listener);
    return () => {
      this.#listeners = this.#listeners.filter((l) => l !== listener);
    };
  };

  #connect = async (): Promise<{ accounts: readonly WalletAccount[] }> => {
    this.#connected = this.#account;
    this.#emit();
    return { accounts: this.accounts };
  };

  #disconnect = async (): Promise<void> => {
    this.#connected = null;
    this.#emit();
  };

  /**
   * Sign the serialized transaction, leaving every other signature in place.
   *
   * A build arrives with its ephemeral signers already applied — the mint
   * keypair at tokenize, the administrator's co-signature on a tokenized
   * install — so replacing the signature array would throw them away and the
   * submit would fail verification. `partiallySignTransaction` merges into the
   * existing map, which is what a real wallet does too.
   */
  #signTransaction = async (
    ...inputs: readonly SolanaSignTransactionInput[]
  ): Promise<readonly SolanaSignTransactionOutput[]> => {
    if (!this.#connected) throw new Error(`${this.name} is not connected`);
    return Promise.all(
      inputs.map(async ({ transaction }) => {
        const decoded = getTransactionDecoder().decode(new Uint8Array(transaction));
        const signed = await partiallySignTransaction([this.#keyPair], decoded);
        return { signedTransaction: new Uint8Array(getTransactionEncoder().encode(signed)) };
      }),
    );
  };

  #signMessage = async (
    ...inputs: readonly SolanaSignMessageInput[]
  ): Promise<readonly SolanaSignMessageOutput[]> => {
    if (!this.#connected) throw new Error(`${this.name} is not connected`);
    return Promise.all(
      inputs.map(async ({ message }) => ({
        signedMessage: message,
        signature: new Uint8Array(await signBytes(this.#keyPair.privateKey, new Uint8Array(message))),
      })),
    );
  };

  get features() {
    return {
      [StandardConnect]: { version: "1.0.0" as const, connect: this.#connect },
      [StandardDisconnect]: { version: "1.0.0" as const, disconnect: this.#disconnect },
      [StandardEvents]: { version: "1.0.0" as const, on: this.#on },
      [SolanaSignTransaction]: {
        version: "1.0.0" as const,
        supportedTransactionVersions: ["legacy", 0] as const,
        signTransaction: this.#signTransaction,
      },
      [SolanaSignMessage]: { version: "1.0.0" as const, signMessage: this.#signMessage },
    };
  }
}

let registered = false;

/**
 * Register the dev keypairs, once, and only on a fork.
 *
 * Idempotent because the Wallet Standard has no way to unregister: a second
 * call would leave two entries per keypair in the picker, and the fork answer
 * arrives asynchronously, so this *will* be called again on a re-render.
 */
export async function registerDevWallets(isFork: boolean): Promise<void> {
  if (!isFork || registered) return;
  const secrets = devSecrets();
  if (secrets.length === 0) return;
  registered = true;
  // Before any DevWallet is constructed: its icon is baked in at construction.
  await loadAvatarStyle(getAvatarStyleId());
  for (const { name, secret } of secrets) {
    const bytes = new Uint8Array(getBase58Encoder().encode(secret));
    const keyPair = await createKeyPairFromBytes(bytes);
    const address = await getAddressFromPublicKey(keyPair.publicKey);
    const publicKey = new Uint8Array(
      await crypto.subtle.exportKey("raw", keyPair.publicKey),
    );
    registerWallet(new DevWallet(`dev:${name}`, keyPair, address, publicKey));
  }
}
