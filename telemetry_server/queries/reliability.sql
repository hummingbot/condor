-- Q4: What is broken out there?
--
-- `error_rate` is errors per event per install-day, so a single install in a
-- crash loop shows up as a bad day rather than drowning the fleet average.

SELECT day, value AS errors_per_event
FROM daily_metrics
WHERE metric = 'error_rate'
ORDER BY day;

-- Failure groups, keyed by exception type and the hash of the message. The
-- message itself is never transmitted (FEAT-023) — grouping works off the hash.
SELECT dim AS exc_type_and_sig, value AS occurrences
FROM daily_metrics
WHERE metric = 'error_group'
ORDER BY value DESC;

-- Which upstream is failing: the Hummingbot API, Gateway, an LLM, or Telegram.
SELECT dim AS service, value AS failures
FROM daily_metrics
WHERE metric = 'upstream_error_rank'
ORDER BY value DESC;

-- Client-side rate limiting must never look like a quiet week. `dropped` is the
-- emitter's own confession, and charting it next to volume is what keeps a
-- taxonomy change or an error flood visible instead of silent.
SELECT day, metric, value
FROM daily_metrics
WHERE metric IN ('dropped_total', 'dropped_rate')
ORDER BY day;
