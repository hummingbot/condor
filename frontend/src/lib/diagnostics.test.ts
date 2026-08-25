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

import { redact } from "@/lib/diagnostics";

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
