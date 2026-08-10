-- Daily totals (fast) — default for GROUP BY dags / dashboards
-- Row-level (per item × day) — use v_sim_optimal_plan_detail

CREATE TABLE IF NOT EXISTS sim_optimal_plan_daily (
    dags DATE NOT NULL PRIMARY KEY,
    inv_value DECIMAL(18, 2) NOT NULL DEFAULT 0,
    inventory_cost DECIMAL(18, 6) NOT NULL DEFAULT 0,
    fixed_shipping_cost DECIMAL(18, 2) NOT NULL DEFAULT 0
) ENGINE=InnoDB;

CREATE INDEX IF NOT EXISTS idx_sim_result_sim_date ON sim_result (sim_date);
CREATE INDEX IF NOT EXISTS idx_sim_result_item_sim_date ON sim_result (item_id, sim_date);

CREATE OR REPLACE VIEW v_sim_optimal_plan AS
SELECT dags, inv_value, inventory_cost, fixed_shipping_cost
FROM sim_optimal_plan_daily;

CREATE OR REPLACE VIEW v_sim_optimal_plan_by_day AS
SELECT dags, inv_value, inventory_cost, fixed_shipping_cost
FROM sim_optimal_plan_daily;

CREATE OR REPLACE VIEW v_sim_optimal_plan_detail AS
SELECT
    sr.item_id AS item_id,
    sr.sim_date AS dags,
    (COALESCE(sr.inv, 0) * COALESCE(NULLIF(i.unit_cost, 0), NULLIF(i.price, 0), 0)) AS inv_value,
    (COALESCE(sr.inv, 0) * COALESCE(NULLIF(i.unit_cost, 0), NULLIF(i.price, 0), 0) * (0.18 / 365)) AS inventory_cost,
    CASE
        WHEN COALESCE(sr.deliveries, 0) > 0 THEN 90.0
        ELSE 0.0
    END AS fixed_shipping_cost
FROM sim_result sr
INNER JOIN items i ON i.id = sr.item_id;

-- Fast (reads ~900 rows, not millions):
--   SELECT dags, SUM(inv_value), SUM(inventory_cost), SUM(fixed_shipping_cost)
--   FROM v_sim_optimal_plan
--   WHERE dags > CURDATE()
--   GROUP BY dags;
