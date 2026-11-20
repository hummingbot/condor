import { useEffect, useState } from "react";

/** How long the socket has to stay down before the UI says so. */
export const CONNECTION_LOST_DELAY_MS = 1500;

/**
 * Has the connection been down long enough to be worth saying (FU-03)?
 *
 * The chat socket drops and comes straight back on a token refresh, a server
 * reload or a network blip, and a banner that flashed for each of those would
 * teach the user to ignore it. So `false` is reported at once and `true` only
 * after the socket has stayed down for `delayMs`.
 */
export function useConnectionLost(
  isConnected: boolean,
  delayMs: number = CONNECTION_LOST_DELAY_MS,
): boolean {
  const [lost, setLost] = useState(false);

  useEffect(() => {
    if (isConnected) return;
    const timer = setTimeout(() => setLost(true), delayMs);
    return () => {
      clearTimeout(timer);
      // Back up (or unmounting): the next drop waits out its own delay.
      setLost(false);
    };
  }, [isConnected, delayMs]);

  return lost && !isConnected;
}
