---
on_kill_switch: keep_spot_close_perp   # flatten_all | keep_spot_close_perp | keep_all
cancel_open_orders: true               # cancel resting orders during winddown
---
# Emergency shutdown (LLM judgment layer)

The deterministic winddown has already applied the policy above: it stopped this
session's executors (closing perp positions, keeping spot) and attempted to close
any orphan positions the policy says to close. You are the best-effort cleanup pass
running on top of that guaranteed floor. Now:

- If any position is spot dust worth less than ~$5, leave it — not worth the fees.
- Cancel any stray Gateway / LP / resting orders you can find for this session.
- If any position that should be closed is still open, close it (stop its executor
  with keep_position off, or place a reduce-only order).
- Notify the owner via `send_notification` with the final realized PnL and a short
  summary of what you wound down.

Be decisive and quick — you are on a strict time budget and the safety-critical
work is already done.
