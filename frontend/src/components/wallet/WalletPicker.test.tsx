/**
 * The picker connects by connector *id*, never by display name.
 *
 * ConnectorKit keys a session on an id it derives from the wallet's name —
 * `Phantom` becomes `wallet-standard:phantom` — and `connect()` accepts
 * nothing else. A row that handed back the name looked right in every
 * screenshot and threw on every click, and the same id is what
 * `localStorage` remembers, so getting it wrong also breaks the silent
 * reconnect on the next load. Both are pinned here.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AvailableWallet } from "@/lib/wallet/context";

import { WalletPicker } from "./WalletPicker";

const WALLETS: AvailableWallet[] = [
  { id: "wallet-standard:phantom", name: "Phantom", icon: "" },
  { id: "wallet-standard:dev-alice", name: "dev:alice", icon: "" },
];

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  localStorage.clear();
});

function render(available: AvailableWallet[], onConnect: (id: string) => Promise<void>) {
  act(() => {
    root.render(
      <WalletPicker available={available} onConnect={onConnect} onClose={() => {}} />,
    );
  });
}

function rowFor(label: string): HTMLButtonElement {
  const row = [...container.querySelectorAll("button")].find((button) =>
    button.textContent?.includes(label),
  );
  if (!row) throw new Error(`no row for ${label}`);
  return row as HTMLButtonElement;
}

describe("WalletPicker", () => {
  it("connects an installed wallet by its connector id", async () => {
    const onConnect = vi.fn(async () => {});
    render(WALLETS, onConnect);

    await act(async () => {
      rowFor("Phantom").click();
    });

    expect(onConnect).toHaveBeenCalledWith("wallet-standard:phantom");
  });

  it("connects a dev keypair by its connector id, not its label", async () => {
    const onConnect = vi.fn(async () => {});
    render(WALLETS, onConnect);

    // The row is labelled `alice` — the `dev:` prefix is stripped for display
    // and is not part of anything `connect` would accept.
    await act(async () => {
      rowFor("alice").click();
    });

    expect(onConnect).toHaveBeenCalledWith("wallet-standard:dev-alice");
  });

  it("marks the remembered connector as Recent by id", () => {
    localStorage.setItem("condor.wallet.name", "wallet-standard:phantom");
    render(WALLETS, async () => {});

    expect(rowFor("Phantom").textContent).toContain("Recent");
    expect(rowFor("alice").textContent).not.toContain("Recent");
  });

  it("offers somewhere to go when the browser has no wallet at all", () => {
    render([], async () => {});

    expect(container.textContent).toContain("No Solana wallet in this browser");
    const links = [...container.querySelectorAll("a")].map((a) => a.textContent);
    expect(links).toContain("Phantom");
  });
});
