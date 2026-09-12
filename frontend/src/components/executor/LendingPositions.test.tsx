import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { LendingPositions } from "./LendingPositions";

it("keeps completed contributions visible and separates wallet holdings from controller attribution", () => {
  const client = new QueryClient();
  client.setQueryData(["lending-positions", "local"], { positions: [{
    account_name: "master", controller_id: "reserves", chain_id: 8453,
    wallet: "wallet", pool: "pool", asset: "asset", symbol: "USDC", decimals: 6,
    net_contributed_raw: "100000000", wallet_receipt_balance_raw: "200000020",
    pending_supply_raw: "0", pending_withdraw_raw: "0", unresolved_executor_ids: [],
    balance_status: "verified_wallet_balance",
  }] });
  const html = renderToStaticMarkup(<QueryClientProvider client={client}><LendingPositions server="local" /></QueryClientProvider>);
  expect(html).toContain("Net contributions: 100");
  expect(html).toContain("Wallet receipt-token balance: 200.00002");
  expect(html).toContain("receipt-token balances cover the entire wallet");
  expect(html).not.toContain("profit");
});
