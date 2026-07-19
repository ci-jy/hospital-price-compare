-- stg_rate -> fact_rate. Expects dim_service, map_payer_plan and dim_hospital
-- to be filled already. Every rate gets a pricing method and a quality flag.

CREATE OR REPLACE MACRO parse_num(x) AS
    TRY_CAST(nullif(regexp_replace(trim(x), '[$,%\s]', '', 'g'), '') AS DOUBLE);

DELETE FROM fact_rate;

INSERT INTO fact_rate
WITH parsed AS (
    SELECT s.*,
           parse_num(s.negotiated_dollar) AS d_raw,
           parse_num(s.negotiated_percentage) AS pct_raw,
           parse_num(s.gross_charge) AS gross,
           coalesce(s.methodology, '') AS m,
           coalesce(lower(s.negotiated_algorithm), '') AS alg
    FROM stg_rate s
),
classified AS (
    SELECT *,
        CASE
            WHEN m LIKE '%per diem%' THEN 'per_diem'
            WHEN m LIKE '%case rate%' THEN 'case_rate'
            WHEN m LIKE '%percent%' THEN 'percent_of_charges'
            WHEN m = 'fee schedule' THEN 'flat_dollar'
            WHEN alg LIKE '%per diem%' OR alg LIKE '%per day%' THEN 'per_diem'
            WHEN alg LIKE '%case rate%' THEN 'case_rate'
            WHEN pct_raw IS NOT NULL AND d_raw IS NULL THEN 'percent_of_charges'
            WHEN d_raw IS NULL AND (alg LIKE '%percent%' OR alg LIKE '%\% of%' ESCAPE '\'
                                    OR alg LIKE '%billed charges%') THEN 'percent_of_charges'
            WHEN d_raw IS NOT NULL THEN 'flat_dollar'
            ELSE 'other'
        END AS pricing_method,
        m NOT IN ('fee schedule', 'case rate', 'per diem',
                  'percent of total billed charges') AS method_inferred
    FROM parsed
),
repaired AS (
    -- A percent-of-charges rate whose only number is in the dollar column
    -- and is at most 1 (e.g. "$0.76") is a fraction of charges, not 76 cents.
    SELECT *,
        (pricing_method = 'percent_of_charges' AND pct_raw IS NULL
         AND d_raw > 0 AND d_raw <= 1 AND coalesce(gross, 2) > 1) AS pct_in_dollar,
        (pct_raw > 0 AND pct_raw <= 1) AS pct_fraction
    FROM classified
),
amounts AS (
    SELECT *,
        CASE WHEN pct_in_dollar THEN NULL ELSE d_raw END AS dollar,
        CASE WHEN pct_in_dollar THEN d_raw * 100
             WHEN pct_fraction THEN pct_raw * 100
             ELSE pct_raw END AS pct
    FROM repaired
),
final AS (
    SELECT *,
        CASE WHEN pricing_method = 'percent_of_charges' THEN pct ELSE dollar END AS amount
    FROM amounts
),
flagged AS (
    SELECT f.*, mp.plan_id, mp.match_method,
        list_filter([
            CASE WHEN pct_in_dollar THEN 'pct_in_dollar_field' END,
            CASE WHEN pct_fraction THEN 'pct_rescaled' END,
            CASE WHEN pricing_method = 'other' THEN 'method_unknown' END,
            CASE WHEN amount IS NULL THEN 'no_amount' END,
            CASE WHEN amount = 0 THEN 'zero_amount' END,
            CASE WHEN pricing_method IN ('flat_dollar', 'case_rate') AND gross > 0
                      AND amount > gross * 1.0001 THEN 'above_gross' END,
            CASE WHEN pricing_method = 'percent_of_charges' AND amount > 100
                 THEN 'pct_over_100' END,
            CASE WHEN mp.match_method = 'unresolved' THEN 'payer_unresolved' END,
            CASE WHEN method_inferred THEN 'method_inferred' END
        ], x -> x IS NOT NULL) AS flags
    FROM final f
    JOIN map_payer_plan mp
      ON mp.hospital_id = f.hospital_id
     AND mp.payer_raw = coalesce(f.payer_name, '')
     AND mp.plan_raw = coalesce(f.plan_name, '')
)
SELECT
    fl.hospital_id, sv.service_id, fl.plan_id,
    coalesce(fl.payer_name, ''), coalesce(fl.plan_name, ''),
    fl.setting, fl.billing_class, fl.modifiers, fl.gross,
    fl.dollar, fl.pct, nullif(fl.m, ''), fl.pricing_method,
    CASE WHEN fl.amount > 0 AND fl.pricing_method <> 'other' THEN fl.amount END,
    CASE WHEN fl.pricing_method = 'percent_of_charges' THEN 'pct_of_charges' ELSE 'usd' END,
    coalesce(fl.flags[1], 'ok'),
    nullif(array_to_string(fl.flags, ','), '')
FROM flagged fl
JOIN dim_service sv ON sv.code = fl.code AND sv.code_type = fl.code_type;
