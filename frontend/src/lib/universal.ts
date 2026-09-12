export interface DefiMarket {
  address: string; label: string; asset: string; decimals: number; share_mint: string;
  share_decimals?: number; base_asset?: string; base_decimals?: number;
  program_id: string; created_at?: string; farm?: string;
  base_reserve_raw?: string; quote_reserve_raw?: string;
  fees?: Record<string, string>;
}
export interface DefiVenue { id: string; name: string; kind: string; example_market: string; example_label: string; source: string }
export interface DefiResult {
  venues?: DefiVenue[]; venue?: string; wallet?: string; cluster?: string; local_mirror?: boolean;
  market?: DefiMarket; position?: Record<string, string>; warnings?: string[];
  instructions?: Record<string, unknown>[]; expires_at?: number;
  maximum_inputs?: Array<{ asset: string; amount_raw: string; decimals: number }>;
  minimum_outputs?: Array<{ asset: string; amount_raw: string; decimals: number }>;
}
export interface DefiResponse { wallet: string; cluster: string; result: DefiResult }

export function rawUnits(value: string, decimals: number): string {
  if (!Number.isInteger(decimals) || decimals < 0 || decimals > 18 || !/^(0|[1-9]\d*)(\.\d+)?$/.test(value)) {
    throw new Error("Enter a non-negative decimal amount.");
  }
  const [whole, fraction = ""] = value.split(".");
  if (fraction.length > decimals) throw new Error(`This amount supports at most ${decimals} decimal places.`);
  return BigInt(whole + fraction.padEnd(decimals, "0")).toString();
}

export function displayUnits(value: string, decimals: number): string {
  if (!/^\d+$/.test(value) || !Number.isInteger(decimals) || decimals < 0 || decimals > 255) return value;
  if (!decimals) return value;
  const digits = value.padStart(decimals + 1, "0");
  const tail = digits.slice(-decimals).replace(/0+$/, "");
  return digits.slice(0, -decimals) + (tail ? `.${tail}` : "");
}

export function networkFeeLimit(value: string): number {
  const raw = BigInt(rawUnits(value, 9));
  if (raw > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error("Network fee limit is too large.");
  return Number(raw);
}

export function spendingPolicy(response: DefiResponse, action: "deposit" | "withdraw",
  budgets: { asset: string; base: string; native: string }) {
  const market = response.result.market;
  if (!market || !response.result.instructions?.length) throw new Error("Prepared market or instructions are missing.");
  const limits: Record<string, string> = { native: rawUnits(budgets.native, 9) };
  if (action === "deposit") {
    limits[market.asset] = rawUnits(budgets.asset, market.decimals);
    if (market.base_asset && market.base_asset !== market.asset) {
      if (market.base_decimals === undefined) throw new Error("Base-token decimals are unavailable.");
      limits[market.base_asset] = rawUnits(budgets.base, market.base_decimals);
    }
  } else {
    const position = response.result.position;
    // Economic farm shares can be fractional and are not wallet-held tokens.
    // A withdrawal that unstakes then burns may have zero net wallet debit.
    const shares = position?.wallet_shares_raw ?? position?.shares_raw;
    if (shares === undefined) throw new Error("Exact wallet share balance is unavailable.");
    limits[market.share_mint] = shares;
  }
  for (const value of Object.values(limits)) {
    if (typeof value !== "string" || !/^\d+$/.test(value) || BigInt(value) > 2n ** 64n - 1n) throw new Error("Asset limit exceeds supported raw units.");
  }
  const programs = response.result.instructions.flatMap(batch => {
    if (!Array.isArray(batch.instructions)) throw new Error("Prepared instruction batch is malformed.");
    return batch.instructions.map((ix: unknown) => {
      if (!ix || typeof ix !== "object" || !("program_id" in ix) || typeof ix.program_id !== "string" || !ix.program_id) {
        throw new Error("Prepared instruction program is missing.");
      }
      return ix.program_id;
    });
  });
  return { wallet: response.wallet, market: market.address, protocol_program: market.program_id,
    allowed_programs: [...new Set(programs)], max_debits_raw: limits };
}
