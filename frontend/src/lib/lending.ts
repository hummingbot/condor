// Deployment and decimals: https://github.com/bgd-labs/aave-address-book/blob/main/src/AaveV3Base.sol
export const BASE_USDC = {
  chain_id: 8453,
  pool: "0xa238dd80c259a72e81d7e4664a9801593f98d1c5",
  asset: "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
} as const;

export function lendingConfig(wallet: string, amount: string, action: "supply" | "withdraw", gas: string) {
  if (!/^0x[0-9a-fA-F]{40}$/.test(wallet)) throw new Error("Enter the Aomi signing wallet address.");
  if (!/^\d+(\.\d{1,6})?$/.test(amount)) throw new Error("Enter a USDC amount with at most 6 decimal places.");
  const [whole, fraction = ""] = amount.split(".");
  const raw = BigInt(whole + fraction.padEnd(6, "0"));
  if (raw <= 0n || raw >= 2n ** 256n - 1n) throw new Error("Enter a positive, bounded USDC amount.");
  if (!/^\d+(\.\d+)?$/.test(gas) || !Number.isFinite(Number(gas)) || Number(gas) <= 0) {
    throw new Error("Enter a positive gas estimate limit in USDT.");
  }
  return {
    chain: "evm", chain_id: BASE_USDC.chain_id, mode: "lending",
    lending: { ...BASE_USDC, wallet: wallet.toLowerCase(), amount: raw.toString(), action },
    max_gas_quote: gas, trading_pair: "USDC-USDT", timeout_sec: 120,
  };
}
