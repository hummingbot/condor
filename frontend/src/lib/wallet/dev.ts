/**
 * Throwaway keypairs, for the fork and nowhere else.
 *
 * Developing the vault flows against a browser wallet means approving four
 * dialogs per create, per attempt. A dev keypair skips that — and is exactly
 * the thing that must never touch mainnet, because its secret is in an env var
 * that ends up in a build.
 *
 * So the gate is not a convention or a warning: `devWallets` returns nothing
 * unless the chain endpoint has *said* `surfpool` (plan M2). The check is the
 * backend's `/settings/gateway/chain`, which asks Gateway which RPC it uses and
 * asks that RPC what it is — so "am I on a fork?" is answered by the node the
 * transaction would actually land on, not by a build flag someone forgot.
 */
import { Keypair, VersionedTransaction, Transaction } from "@solana/web3.js";
import nacl from "tweetnacl";

import type { ConnectedWallet } from "./standard";

/**
 * `VITE_DEV_WALLET_SECRET_<NAME>=<base58 secret key>`.
 *
 * Read from `import.meta.env` at build time, which is why this is dev-only in
 * the strongest sense: the secrets are in the bundle. Two of them make a
 * two-holder redemption testable without two browsers.
 */
function devSecrets(): { name: string; secret: string }[] {
  const prefix = "VITE_DEV_WALLET_SECRET_";
  return Object.entries(import.meta.env)
    .filter(([key, value]) => key.startsWith(prefix) && typeof value === "string" && value)
    .map(([key, value]) => ({ name: key.slice(prefix.length).toLowerCase(), secret: value as string }));
}

function decodeBase58(value: string): Uint8Array {
  const ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
  let num = 0n;
  for (const char of value) {
    const digit = ALPHABET.indexOf(char);
    if (digit < 0) throw new Error(`"${char}" is not base58`);
    num = num * 58n + BigInt(digit);
  }
  const bytes: number[] = [];
  while (num > 0n) {
    bytes.unshift(Number(num & 0xffn));
    num >>= 8n;
  }
  for (const char of value) {
    if (char !== "1") break;
    bytes.unshift(0);
  }
  return Uint8Array.from(bytes);
}

/**
 * Sign a serialized transaction with a keypair, leaving every other signature
 * in place.
 *
 * A build arrives with the ephemeral signers already applied — the mint
 * keypair at tokenize, the administrator's co-signature on a tokenized
 * install — so re-serializing with only this signature would throw them away
 * and the submit would fail its verification. `sign` on a deserialized
 * transaction merges into the existing signature array, which is what a real
 * wallet does too.
 */
function signSerialized(keypair: Keypair, serialized: Uint8Array): Uint8Array {
  try {
    const tx = VersionedTransaction.deserialize(serialized);
    tx.sign([keypair]);
    return tx.serialize();
  } catch {
    const tx = Transaction.from(serialized);
    tx.partialSign(keypair);
    return new Uint8Array(tx.serialize({ requireAllSignatures: false }));
  }
}

/**
 * The dev keypairs, as wallets — but only on a fork.
 *
 * `isFork` comes from the caller, which got it from the chain badge endpoint.
 * Passing `false`, or not knowing yet, yields an empty list: the failure mode
 * of "we could not tell" has to be "no dev wallets", not "probably fine".
 */
export function devWallets(isFork: boolean): ConnectedWallet[] {
  if (!isFork) return [];
  return devSecrets().map(({ name, secret }) => {
    const keypair = Keypair.fromSecretKey(decodeBase58(secret));
    return {
      name: `dev:${name}`,
      icon: "",
      address: keypair.publicKey.toBase58(),
      async signTransaction(transaction: Uint8Array) {
        return signSerialized(keypair, transaction);
      },
      async signMessage(message: Uint8Array) {
        return nacl.sign.detached(message, keypair.secretKey);
      },
    };
  });
}
