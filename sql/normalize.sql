-- stg_rate -> fact_rate. Expects dim_service, map_payer_plan and dim_hospital
-- to be filled already, and the rate_classified view from hpc/method.py
-- (pricing method, compare amount and value checks per staged row). Every
-- rate gets a quality flag.

DELETE FROM fact_rate;

INSERT INTO fact_rate
WITH flagged AS (
    SELECT f.*, mp.plan_id, mp.match_method,
        list_filter([
            CASE WHEN pct_in_dollar THEN 'pct_in_dollar_field' END,
            CASE WHEN pct_fraction THEN 'pct_rescaled' END,
            CASE WHEN pct_derived THEN 'pct_derived' END,
            CASE WHEN method_value_mismatch THEN 'method_value_mismatch' END,
            CASE WHEN pct_of_fee_schedule THEN 'pct_of_fee_schedule' END,
            CASE WHEN pricing_method = 'other' THEN 'method_unknown' END,
            CASE WHEN amount IS NULL THEN 'no_amount' END,
            CASE WHEN amount = 0 THEN 'zero_amount' END,
            CASE WHEN pricing_method IN ('flat_dollar', 'case_rate') AND gross > 0
                      AND amount > gross * 1.0001 THEN 'above_gross' END,
            CASE WHEN pricing_method = 'percent_of_charges' AND amount > 100
                 THEN 'pct_over_100' END,
            CASE WHEN mp.match_method = 'unresolved' THEN 'payer_unresolved' END,
            CASE WHEN mp.match_method = 'ambiguous' THEN 'plan_type_ambiguous' END,
            CASE WHEN method_inferred THEN 'method_inferred' END,
            CASE WHEN f.billing_class IS NULL THEN 'billing_class_assumed' END
        ], x -> x IS NOT NULL) AS flags
    FROM rate_classified f
    JOIN map_payer_plan mp
      ON mp.hospital_id = f.hospital_id
     AND mp.payer_raw = coalesce(f.payer_name, '')
     AND mp.plan_raw = coalesce(f.plan_name, '')
)
SELECT
    fl.hospital_id, sv.service_id, fl.plan_id,
    coalesce(fl.payer_name, ''), coalesce(fl.plan_name, ''),
    -- Files without a billing_class column publish the hospital's own
    -- (facility) charges.
    fl.setting, coalesce(fl.billing_class, 'facility'), fl.modifiers, fl.gross,
    fl.dollar, fl.pct, nullif(fl.m, ''), fl.pricing_method,
    CASE WHEN fl.amount > 0 AND fl.pricing_method <> 'other' THEN fl.amount END,
    CASE WHEN fl.pricing_method = 'percent_of_charges' THEN 'pct_of_charges' ELSE 'usd' END,
    coalesce(fl.flags[1], 'ok'),
    nullif(array_to_string(fl.flags, ','), '')
FROM flagged fl
JOIN dim_service sv ON sv.code = fl.code AND sv.code_type = fl.code_type;
