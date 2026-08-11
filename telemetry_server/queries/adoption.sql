-- Q1: How many installs are alive, on what, and how fast do upgrades spread?
--
-- Reads `daily_metrics` only, so it stays correct after raw events expire and
-- costs a few thousand rows rather than a full scan. Every query in this
-- directory is plain ANSI: it runs against the collector's Postgres and against
-- the SQLite the test suite seeds, and it is readable without Grafana.

SELECT day, metric, dim, value
FROM daily_metrics
WHERE metric IN ('dau', 'wau', 'mau')
ORDER BY day, metric;

-- Version and OS mix, snapshotted once per rollup. The series of snapshots is
-- what answers "how long does an upgrade take to propagate" — a version's share
-- decaying across consecutive days is the propagation curve.
SELECT day, metric, dim AS value_of, value
FROM daily_metrics
WHERE metric IN ('version_share', 'os_share', 'provider_share')
ORDER BY day, metric, value DESC;
