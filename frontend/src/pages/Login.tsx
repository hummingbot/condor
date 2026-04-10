import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { useAuth } from "@/lib/auth";

export function Login() {
  const { isAuthenticated, loginWithToken } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [error, setError] = useState("");
  const [loggingIn, setLoggingIn] = useState(false);
  const attempted = useRef(false);

  useEffect(() => {
    if (isAuthenticated) {
      navigate("/", { replace: true });
    }
  }, [isAuthenticated, navigate]);

  // Auto-login when ?token= is present in URL
  useEffect(() => {
    const token = searchParams.get("token");
    if (!token || attempted.current) return;
    attempted.current = true;
    setLoggingIn(true);

    loginWithToken(token)
      .then(() => navigate("/", { replace: true }))
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Login failed");
        setLoggingIn(false);
      });
  }, [searchParams, loginWithToken, navigate]);

  return (
    <div className="flex h-screen items-center justify-center">
      <div className="w-full max-w-sm rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-8 text-center">
        <img src="/condor.jpeg" alt="Condor" className="mx-auto mb-4 h-16 w-16 rounded-full" />
        <h1 className="mb-2 text-2xl font-bold">Condor</h1>
        {loggingIn ? (
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
