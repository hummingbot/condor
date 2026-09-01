/**
 * `redact()` is the only automated control over text this app puts into a
 * public GitHub issue, and its pattern list has already been widened twice
 * (SEC-209, SEC-210). Each widening was verified by hand and left nothing
 * behind. These cases are that missing guard: every shape the list is meant to
 * catch, and — just as important — the prose and query keys it must not eat,
 * since an over-match destroys the diagnostics the report exists to carry.
 *
 * Pure text in, pure text out: no DOM, no React, so this runs under vitest's
 * `node` environment (see vite.config.ts).
 */

import { describe, expect, it } from "vitest";

import { areaForRoute, redact } from "@/lib/diagnostics";
import { AREAS, describePage } from "@/lib/page-context";

const HEX64 = "a".repeat(64);

describe("redact", () => {
  describe("the credential names SEC-210 added", () => {
    it.each([
      ["private_key", `private_key: 0x${HEX64}`],
      ["camelCase", `privateKey=0x${HEX64}`],
      ["kebab-case", `private-key: 0x${HEX64}`],
      ["a trailing word", "secret_key=s3cr3tvalue"],
      ["credential", "credential: hunter2hunter2"],
      ["seed", "seed: 9f8e7d6c5b4a"],
    ])("masks %s", (_name, text) => {
      const out = redact(text);
      expect(out).not.toContain(HEX64);
      expect(out).not.toMatch(/s3cr3tvalue|hunter2hunter2|9f8e7d6c5b4a/);
      expect(out).toContain("***");
    });

    it("masks a whole mnemonic, not just its first word", () => {
      const phrase =
        "legal winner thank year wave sausage worth useful legal winner thank yellow";
      const out = redact(`mnemonic: ${phrase}`);
      expect(out).toBe("mnemonic: ***");
      expect(out).not.toContain("yellow");
    });

    it("masks a seed_phrase the same way", () => {
      expect(redact("seed_phrase = one two three four five six")).toBe(
        "seed_phrase = ***",
      );
    });
  });

  describe("the shapes that predate SEC-210", () => {
    it.each([
      ["api_key", "api_key: AKIAIOSFODNN7EXAMPLE"],
      ["apiKey", 'apiKey="AKIAIOSFODNN7EXAMPLE"'],
      ["password", "password: correct-horse"],
      ["passphrase", "passphrase=correct-horse"],
      ["token", "token: ghp_016C7e42F292c6912E7710c838347Ae1"],
    ])("masks %s", (_name, text) => {
      const out = redact(text);
      expect(out).toContain("***");
      expect(out).not.toMatch(/AKIAIOSFODNN7EXAMPLE|correct-horse|ghp_/);
    });

    // The scheme word goes too: the bearer rule masks the token first, and the
    // 6-character word "Bearer" is then itself a long-enough value for the
    // name/assignment rule to take. Cosmetic — the secret is what matters —
    // but recorded here so a future tightening is a deliberate change, not a
    // surprise.
    it("masks a bearer token", () => {
      const out = redact("Authorization: Bearer abcdef0123456789");
      expect(out).not.toContain("abcdef0123456789");
      expect(out).toBe("Authorization: *** ***");
    });

    it("masks the credential behind a Basic scheme, not the word Basic", () => {
      const out = redact("Authorization: Basic dXNlcjpwYXNzd29yZA==");
      expect(out).toBe("Authorization: Basic ***");
    });

    it("masks a JWT wherever it appears", () => {
      const out = redact(
        "401 for eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.sig",
      );
      expect(out).toBe("401 for ***jwt***");
    });

    it("masks an RPC key carried as a bare path segment", () => {
      expect(
        redact("wss://eth-mainnet.g.alchemy.com/v2/kR3n8Tq1zP7wYb42Xd9L failed"),
      ).toBe("wss://eth-mainnet.g.alchemy.com/v2/*** failed");
    });
  });

  describe("what it must leave alone", () => {
    it("keeps prose that merely names a credential", () => {
      const prose = "my token expired yesterday and the password reset failed";
      expect(redact(prose)).toBe(prose);
    });

    it("keeps query keys, which failingRequests renders verbatim", () => {
      const line = 'queryKey: ["portfolio-history","binance","1h"] → 500';
      expect(redact(line)).toBe(line);
    });

    it("keeps this app's own API routes", () => {
      const line = "GET https://localhost:8088/api/v1/accounts/state → 500";
      expect(redact(line)).toBe(line);
    });

    it("keeps public chain data — addresses and tx hashes", () => {
      const line = `wallet 0x${"b".repeat(40)} sent tx 0x${HEX64}`;
      expect(redact(line)).toBe(line);
    });

    it("keeps a short value that cannot be a credential", () => {
      expect(redact("token: abc")).toBe("token: abc");
    });
  });

  it("is idempotent, so masking twice on the way out changes nothing", () => {
    const text = [
      "api_key: AKIAIOSFODNN7EXAMPLE",
      "private_key: 0x" + HEX64,
      "Authorization: Bearer abcdef0123456789",
      "mnemonic: one two three four five six seven eight",
      "https://eth-mainnet.g.alchemy.com/v2/kR3n8Tq1zP7wYb42Xd9L",
    ].join("\n");
    const once = redact(text);
    expect(redact(once)).toBe(once);
  });
});

/**
 * The other half of a report's accuracy: `describePage` is the route table the
 * `Page:` line and the dialog's default area are read from, and it drifted away
 * from `App.tsx` once already — the two DEX pages, the newest in the app, filed
 * as a raw pathname under "Dashboard (other)" (READ-236). These cases pin the
 * table to the router: every route App.tsx actually renders a component for
 * gets a name and an area, and nothing claims a path the router redirects.
 */
describe("describePage", () => {
  it.each([
    ["/dex", "DEX pools"],
    ["/dex/solana/7qbRF6YsyGuLUVs6Y1q64bdVrfe4ZcUUz1JRdoVNUJnm", "DEX pool"],
  ])("files %s under Gateway / DEX", (route, name) => {
    const page = describePage(route);
    expect(page.area).toBe("Gateway / DEX");
    expect(areaForRoute(route)).toBe("Gateway / DEX");
    expect(page.page).toContain(name);
  });

  it("names the network and the address of a pool", () => {
    const page = describePage("/dex/solana/7qbRF6YsyGuLUVs6Y1q64bdVrfe4ZcUUz1JRdoVNUJnm");
    expect(page.page).toContain("solana");
    expect(page.page).toContain("7qbRF6YsyGuLUVs6Y1q64bdVrfe4ZcUUz1JRdoVNUJnm");
  });

  // Every path App.tsx renders a real component for, inside the shell that
  // mounts the dialog and the error boundary. `/login` is deliberately absent:
  // it lives outside `ProtectedRoute`, so no consumer of this module can run
  // there.
  it.each([
    ["/", "Agents & chat"],
    ["/portfolio", "Portfolio"],
    ["/bots", "Bots & controllers"],
    ["/bots/hummingbot-1", "Bots & controllers"],
    ["/trade", "Trading & executors"],
    ["/dex", "Gateway / DEX"],
    ["/dex/solana/PoolAddr", "Gateway / DEX"],
    ["/executors", "Trading & executors"],
    ["/routines", "Routines & reports"],
    ["/agents/scout", "Agents & chat"],
    ["/agents/scout/strategies/grid", "Agents & chat"],
    ["/settings", "Settings & connections"],
  ])("gives %s a real area, never the catch-all", (route, area) => {
    expect(describePage(route).area).toBe(area);
    expect(describePage(route).area).not.toBe("Dashboard (other)");
    expect(describePage(route).page).not.toBe(route);
  });

  // The drift runs the other way too: a case for a path the router answers with
  // `<Navigate>` is dead code that reads as coverage.
  it.each([
    "/reports",
    "/agents",
    "/archived",
    "/market",
    "/executors/new",
    "/executors/new-grid",
  ])("claims no name for %s, which App.tsx redirects", (route) => {
    const page = describePage(route);
    // `/executors/new*` is covered incidentally by the live `/executors` case;
    // what must not exist is a branch written *for* the redirected path.
    if (route.startsWith("/executors/")) {
      expect(page.page).toBe("Executors");
    } else {
      expect(page.page).toBe(route === "/agents" ? 'Agent detail ("")' : route);
    }
  });

  it("only ever returns an area the issue templates offer", () => {
    for (const route of ["/", "/dex", "/dex/a/b", "/nope", "/settings"]) {
      expect(AREAS).toContain(describePage(route).area);
    }
  });

  it("keeps the tab in the page name, and the query in the route", () => {
    const page = describePage("/dex", "?tab=clmm");
    expect(page.page).toBe('DEX pools · tab "clmm"');
    expect(page.route).toBe("/dex?tab=clmm");
  });
});
