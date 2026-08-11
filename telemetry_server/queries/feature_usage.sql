-- Q3: What is actually used, and what are we maintaining for nobody?
--
-- Note what is absent: nothing groups by `user_hash`. It is salted per install
-- (FEAT-023), so it can be counted inside one install but is meaningless
-- across installs, and a query that grouped by it globally would be inventing
-- a cross-install identity the client deliberately refused to provide.

SELECT day, dim AS event_name, value AS events
FROM daily_metrics
WHERE metric = 'event_rank'
ORDER BY day, value DESC;

-- The key dimension behind each surface: which commands, which modules, which
-- routines, which connectors.
SELECT day, metric, dim AS name, value AS uses
FROM daily_metrics
WHERE metric IN ('command_rank', 'action_rank', 'routine_rank', 'trade_rank')
ORDER BY metric, value DESC;

-- The activation funnel. `feature_first_use` fires once per install, ever, so
-- this counts installs that ever reached a feature, not how often they use it.
SELECT dim AS feature, value AS installs_activated
FROM daily_metrics
WHERE metric = 'activation'
ORDER BY value DESC;
