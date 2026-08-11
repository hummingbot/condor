-- Q5: How are agents actually used, and what does a turn cost?

SELECT day, metric, dim AS value_of, value
FROM daily_metrics
WHERE metric IN ('agent_provider', 'model_rank', 'agent_kind')
ORDER BY metric, value DESC;

-- Outcome mix. A rising `aborted` share is a usability signal; a rising `error`
-- share is a reliability one.
SELECT dim AS outcome, value AS turns
FROM daily_metrics
WHERE metric = 'agent_outcome'
ORDER BY value DESC;

-- What a turn costs, and what a routine costs.
SELECT day, metric, value
FROM daily_metrics
WHERE metric IN ('agent_tool_calls_avg', 'agent_duration_ms_avg',
                 'routine_duration_ms_avg')
ORDER BY day, metric;

-- Dry run versus live, and how often a confirmation is actually granted. A deny
-- or timeout rate that climbs means the agent is asking for the wrong things.
SELECT metric, dim AS value_of, value
FROM daily_metrics
WHERE metric IN ('strategy_mode', 'confirmation_decision')
ORDER BY metric, value DESC;
