/**
 * The two directions are two mechanisms, and the drawer must not confuse them.
 *
 * Funding a vault is a transaction the creator's own wallet signs. Taking money
 * back out is the *delegate* moving what it can already move, which happens on
 * the server and needs no signature. They look like one control with a flip
 * button between them, which is the point — and exactly why it is worth pinning
 * that the flip changes who signs rather than only which way an arrow points.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { VaultInfo } from "@/lib/api";
import type { WalletState } from "@/lib/wallet/context";
import { WalletHarness } from "@/test/walletHarness";

const buildVaultAction = vi.fn();
const withdrawFromVault = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    buildVaultAction: (...a: unknown[]) => buildVaultAction(...a),
    withdrawFromVault: (...a: unknown[]) => withdrawFromVault(...a),
  },
}));

const { TransferDrawer } = await import("./TransferDrawer");

// Only the fields the drawer reads. A whole VaultInfo here would be thirty
// lines of chain state the component never touches.
const VAULT = {
  account: "9NkBz4Cw83zNVdTe6WNTiCtABBcrZC5MTmQxrEKvTQk6",
  treasury_address: "H1SgKcBnH8VcHhEVrBHQUEt8VLYyJvQbYDfMj7ttRA7U",
  creator_address: "CqP6b3XgfXamU3V1fgRvCTLN57fsxMUQKBj34mPwnoxH",
  server: "vaults",
  network: "mainnet-beta",
  label: "Demo vault",
  quote_mint: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
} as unknown as VaultInfo;

const SOL = { symbol: "SOL", vaultAmount: 1.75 };
/** `quote_mint` is nullable on a vault; an asset being moved always has one. */
const USDC_MINT = VAULT.quote_mint as string;

let container: HTMLDivElement;
let root: Root;
const signAndSubmit = vi.fn(async () => "sig-from-browser");

function render() {
  const wallet: WalletState = {
    available: [],
    connected: { id: "dev", name: "dev:demo", icon: "", address: VAULT.creator_address },
    attached: VAULT.creator_address,
    mismatched: false,
    connect: async () => {},
    disconnect: () => {},
    attach: async () => {},
    detach: async () => {},
    signAndSubmit,
    signAndSubmitAll: async () => [],
  };
  act(() => {
    root.render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>
        <WalletHarness value={wallet}>
          <TransferDrawer vault={VAULT} asset={SOL} onClose={() => {}} />
        </WalletHarness>
      </QueryClientProvider>,
    );
  });
}

const button = (text: string) =>
  [...container.querySelectorAll("button")].find((b) => b.textContent?.includes(text)) as
    | HTMLButtonElement
    | undefined;

/** The flip carries an icon and no text, so it is found by what it announces. */
const flip = () =>
  container.querySelector('button[aria-label="Swap direction"]') as HTMLButtonElement;

async function type(value: string) {
  const input = container.querySelector("input") as HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  await act(async () => {
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => {
  buildVaultAction.mockReset().mockResolvedValue({ transaction: "b64", extra_signers: [] });
  withdrawFromVault.mockReset().mockResolvedValue({ signature: "sig-from-server", amount: "1" });
  signAndSubmit.mockClear();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("TransferDrawer", () => {
  it("opens on the way in, because a new vault needs funding before anything else", () => {
    render();

    expect(container.textContent).toContain("Funding the vault. Your wallet signs.");
    expect(button("Deposit SOL")).toBeTruthy();
  });

  it("a deposit is built and signed in the browser", async () => {
    render();
    await type("0.75");
    await act(async () => button("Deposit SOL")!.click());

    expect(buildVaultAction).toHaveBeenCalledWith(VAULT.account, "deposit", { amount: "0.75" });
    expect(signAndSubmit).toHaveBeenCalledWith(VAULT.server, expect.anything(), VAULT.network);
    // The delegate is not involved on the way in.
    expect(withdrawFromVault).not.toHaveBeenCalled();
  });

  it("a withdrawal goes to the server and asks for no signature", async () => {
    render();
    await act(async () => flip().click());
    await type("0.5");
    await act(async () => button("Withdraw SOL")!.click());

    expect(withdrawFromVault).toHaveBeenCalledWith(VAULT.account, {
      destination: VAULT.creator_address,
      amount: "0.5",
      mint: undefined,
    });
    expect(signAndSubmit).not.toHaveBeenCalled();
    expect(buildVaultAction).not.toHaveBeenCalled();
  });

  it("refuses to withdraw more than the vault holds, and says the number", async () => {
    render();
    await act(async () => flip().click());
    await type("99");

    const send = button("The vault holds");
    expect(send?.textContent).toContain("1.75 SOL");
    expect(send?.disabled).toBe(true);
    expect(withdrawFromVault).not.toHaveBeenCalled();
  });

  it("carries the mint for an SPL token, and omits it for native SOL", async () => {
    act(() => root.unmount());
    root = createRoot(container);
    const usdc = { symbol: "USDC", mint: USDC_MINT, vaultAmount: 350 };
    act(() => {
      root.render(
        <QueryClientProvider client={new QueryClient()}>
          <WalletHarness
            value={{
              available: [], connected: { id: "d", name: "d", icon: "", address: VAULT.creator_address },
              attached: VAULT.creator_address, mismatched: false,
              connect: async () => {}, disconnect: () => {}, attach: async () => {}, detach: async () => {},
              signAndSubmit, signAndSubmitAll: async () => [],
            }}
          >
            <TransferDrawer vault={VAULT} asset={usdc} onClose={() => {}} />
          </WalletHarness>
        </QueryClientProvider>,
      );
    });
    await type("100");
    await act(async () => button("Deposit USDC")!.click());

    expect(buildVaultAction).toHaveBeenCalledWith(VAULT.account, "deposit", {
      amount: "100",
      mint: USDC_MINT,
    });
  });
});
