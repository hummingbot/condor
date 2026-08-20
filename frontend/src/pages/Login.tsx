import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { useAuth } from "@/lib/auth";

export function Login() {
  const { isAuthenticated, loginWithToken, loginLocal } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [error, setError] = useState("");
  const [loggingIn, setLoggingIn] = useState(false);
  const attempted = useRef(false);
  // How this install logs in — asked once, before anything is rendered, so the
  // "run /web in Telegram" card never flashes on a machine that has no Telegram.
  const [mode, setMode] = useState<string | null>(null);
  const probed = useRef(false);

  // Where to land after login. Only allow internal paths to avoid open redirects.
  const rawRedirect = searchParams.get("redirect");
  const redirectTo =
    rawRedirect && rawRedirect.startsWith("/") && !rawRedirect.startsWith("//")
      ? rawRedirect
      : "/";

  useEffect(() => {
    if (isAuthenticated) {
      navigate(redirectTo, { replace: true });
    }
  }, [isAuthenticated, navigate, redirectTo]);

  // Auto-login when ?token= is present in URL
  useEffect(() => {
    const token = searchParams.get("token");
    if (!token || attempted.current) return;
    attempted.current = true;
    setLoggingIn(true);

    // Strip the one-time token from the URL so it does not linger in browser
    // history or get leaked via the Referer header. The token is consumed via
    // a POST below; the address bar should not keep it.
    window.history.replaceState(null, "", window.location.pathname);

    loginWithToken(token)
      .then(() => navigate(redirectTo, { replace: true }))
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Login failed");
        setLoggingIn(false);
      });
  }, [searchParams, loginWithToken, navigate, redirectTo]);

  // Local mode (FEAT-049): there is no login step. Ask the server how it
  // authenticates and, if the answer is "locally", claim the session and go.
  // Skipped entirely when a one-time ?token= is being redeemed above.
  useEffect(() => {
    if (searchParams.get("token") || probed.current) return;
    probed.current = true;

    let cancelled = false;
    fetch("/api/v1/auth/mode")
      .then((res) => (res.ok ? res.json() : { mode: "telegram" }))
      .then((data) => {
        if (cancelled) return;
        setMode(data?.mode ?? "telegram");
        if (data?.mode !== "local") return;
        setLoggingIn(true);
        return loginLocal()
          .then(() => navigate(redirectTo, { replace: true }))
          .catch((err) => {
            setError(err instanceof Error ? err.message : "Login failed");
            setLoggingIn(false);
          });
      })
      .catch(() => {
        // The server is unreachable, not Telegram-less: show the normal card.
        if (!cancelled) setMode("telegram");
      });

    return () => {
      cancelled = true;
    };
  }, [searchParams, loginLocal, navigate, redirectTo]);

  return (
    <div className="flex h-screen items-center justify-center">
      <div className="w-full max-w-sm rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-8 text-center">
        <img src="/condor_old.jpeg" alt="Condor" className="mx-auto mb-4 h-16 w-16 rounded-full" />
        <h1 className="mb-2 text-2xl font-bold">Condor</h1>
        {loggingIn || (mode === null && !searchParams.get("token")) ? (
          <p className="text-sm text-[var(--color-text-muted)]">
            Signing in...
          </p>
        ) : (
          <>
            <p className="mb-6 text-sm text-[var(--color-text-muted)]">
              Run the <code className="rounded bg-[var(--color-bg)] px-1.5 py-0.5 font-mono text-xs">/web</code> command in your Telegram bot to get a login link.
            </p>
          </>
        )}
        {error && (
          <p className="mt-4 text-sm text-[var(--color-red)]">{error}</p>
        )}
      </div>
    </div>
  );
}
