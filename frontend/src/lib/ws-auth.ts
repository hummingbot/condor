// auth pass: remove — the backend WS endpoints (chat_ws.py, ws_manager.py)
// still decode a JWT from the Sec-WebSocket-Protocol handshake. Until the
// auth pass deletes that requirement, keep sending any token a previous
// login left in localStorage. Once the backend stops decoding JWTs this
// whole file goes away and WS connects need no subprotocol.

/** Sentinel subprotocol that marks the JWT-carrying handshake. */
export const WS_AUTH_SUBPROTOCOL = "condor-jwt";

/** Legacy JWT from a pre-auth-removal login, if any. */
export function getLegacyWsToken(): string | null {
  return localStorage.getItem("condor_token");
}
