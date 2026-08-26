/**
 * The names that mark a value as a credential.
 *
 * Two lists, not one, because the two consumers fail in opposite directions.
 * `redact()` writes into a public GitHub issue, so a miss leaks a key and an
 * over-match destroys the diagnostics the report exists to carry. The settings
 * form only decides whether an input renders as `type="password"`, so it can
 * afford to guess generously.
 *
 * Kept in step with the backend's own transcript redactor,
 * `condor/runtime/conversations.py:_SECRET_KEY_HINTS` — the two ends of the app
 * should agree on what a credential is called.
 */

/**
 * Credential names safe to match mechanically on text.
 *
 * Mirrors `_SECRET_KEY_HINTS`, plus `seed` (a wallet seed phrase is a
 * credential the dashboard actually handles, via `ImportGatewayWallet`).
 *
 * Bare `key` is deliberately absent: as a text pattern it would mask every
 * `key:`, `queryKey:` and `cache_key:` value in a diagnostics block, and
 * `failingRequests()` renders query keys verbatim.
 */
export const SECRET_KEY_NAMES = [
  "password",
  "passphrase",
  "secret",
  "token",
  "api_key",
  "apikey",
  "private_key",
  "privatekey",
  "mnemonic",
  "seed",
  "credential",
  "authorization",
] as const;

/**
 * Substrings that mark a connector config field as a sensitive credential.
 *
 * Connector keys vary (api_key, secret_key, passphrase, private_key, api_token,
 * mnemonic, seed, ...), so we match by substring on both the field name and
 * type rather than the few exact names ("secret"/"password") covered before.
 * The loose extras are safe here and only here — the cost of a false positive
 * is a field masked in a form the user is typing into anyway.
 */
export const CREDENTIAL_FIELD_PATTERNS: readonly string[] = [
  ...SECRET_KEY_NAMES,
  "key",
  "private",
];
