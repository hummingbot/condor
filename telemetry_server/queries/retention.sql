-- Q2: Do people stay? D1 / D7 / D30 by first-seen cohort.
--
-- `day` here is the cohort's first-seen day, not the day the metric describes.
-- Cohorts are derived from `install_days`, which is written on the ingest path
-- and never expires, so retention survives the 90-day raw retention window.

SELECT
    cohort.day                                   AS cohort_day,
    cohort.value                                 AS installs,
    d1.value                                     AS retained_d1,
    d7.value                                     AS retained_d7,
    d30.value                                    AS retained_d30
FROM daily_metrics AS cohort
LEFT JOIN daily_metrics AS d1
       ON d1.day = cohort.day AND d1.metric = 'retention_d1'
LEFT JOIN daily_metrics AS d7
       ON d7.day = cohort.day AND d7.metric = 'retention_d7'
LEFT JOIN daily_metrics AS d30
       ON d30.day = cohort.day AND d30.metric = 'retention_d30'
WHERE cohort.metric = 'cohort_size'
ORDER BY cohort.day;
