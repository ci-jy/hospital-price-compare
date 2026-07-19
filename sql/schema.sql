-- Normalised star schema for negotiated rates.
--
--   stg_rate      raw rows from every reader, all text, one row per
--                 hospital x item x payer x plan (rebuilt per hospital)
--   dim_hospital  one row per loaded hospital file
--   dim_service   one row per billing code (CPT / HCPCS / DRG)
--   dim_payer     resolved payer (e.g. "Blue Cross of Idaho")
--   dim_plan      resolved payer x plan type (e.g. Blue Cross of Idaho, medicaid)
--   map_payer_plan  each hospital's raw payer/plan names -> dim_plan
--   fact_rate     one negotiated rate with its pricing method and quality flag

CREATE TABLE IF NOT EXISTS stg_rate (
    hospital_id VARCHAR, description VARCHAR, code VARCHAR, code_type VARCHAR,
    setting VARCHAR, billing_class VARCHAR, modifiers VARCHAR,
    gross_charge VARCHAR, cash_price VARCHAR, payer_name VARCHAR, plan_name VARCHAR,
    negotiated_dollar VARCHAR, negotiated_percentage VARCHAR,
    negotiated_algorithm VARCHAR, estimated_amount VARCHAR, methodology VARCHAR,
    notes VARCHAR
);

CREATE TABLE IF NOT EXISTS dim_hospital (
    hospital_id VARCHAR PRIMARY KEY,
    hospital_name VARCHAR NOT NULL,
    city VARCHAR,
    encoding VARCHAR NOT NULL,          -- tall | wide | json
    mrf_url VARCHAR,
    file_hospital_name VARCHAR,
    file_last_updated VARCHAR,
    file_version VARCHAR,
    staged_rows BIGINT,
    loaded_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dim_service (
    service_id INTEGER PRIMARY KEY,
    code VARCHAR NOT NULL,
    code_type VARCHAR NOT NULL,
    description VARCHAR,                -- most common description across files
    raw_descriptions INTEGER            -- how many different ones the files use
);

CREATE TABLE IF NOT EXISTS dim_payer (
    payer_id INTEGER PRIMARY KEY,
    payer_name VARCHAR NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS dim_plan (
    plan_id INTEGER PRIMARY KEY,
    payer_id INTEGER NOT NULL REFERENCES dim_payer (payer_id),
    plan_type VARCHAR NOT NULL,         -- commercial | medicare_advantage | medicaid | ...
    UNIQUE (payer_id, plan_type)
);

CREATE TABLE IF NOT EXISTS map_payer_plan (
    hospital_id VARCHAR NOT NULL,
    payer_raw VARCHAR NOT NULL,
    plan_raw VARCHAR NOT NULL,
    plan_id INTEGER NOT NULL REFERENCES dim_plan (plan_id),
    match_method VARCHAR NOT NULL,      -- reviewed | alias | fuzzy | unresolved
    match_score DOUBLE,
    PRIMARY KEY (hospital_id, payer_raw, plan_raw)
);

CREATE TABLE IF NOT EXISTS fact_rate (
    hospital_id VARCHAR NOT NULL REFERENCES dim_hospital (hospital_id),
    service_id INTEGER NOT NULL REFERENCES dim_service (service_id),
    plan_id INTEGER NOT NULL REFERENCES dim_plan (plan_id),
    payer_raw VARCHAR NOT NULL,
    plan_raw VARCHAR NOT NULL,
    setting VARCHAR,
    billing_class VARCHAR,
    modifiers VARCHAR,
    gross_charge DOUBLE,
    negotiated_dollar DOUBLE,
    negotiated_percentage DOUBLE,
    methodology_raw VARCHAR,
    pricing_method VARCHAR NOT NULL,    -- flat_dollar | percent_of_charges | per_diem | case_rate | other
    compare_amount DOUBLE,              -- dollars, or percent of charges for percent_of_charges
    compare_unit VARCHAR NOT NULL,      -- usd | pct_of_charges
    quality_flag VARCHAR NOT NULL,      -- ok, or the most serious issue found
    quality_notes VARCHAR               -- every issue found, comma separated
);

-- A comparison group is one code in one setting priced one way. Rates are
-- only ever ranked against other rates in the same group.
CREATE OR REPLACE VIEW v_rate_compare AS
SELECT
    s.code,
    s.code_type,
    s.description AS service,
    coalesce(f.setting, 'unknown') AS setting,
    f.pricing_method,
    f.compare_unit,
    h.hospital_id,
    h.hospital_name,
    p.payer_name,
    pl.plan_type,
    f.payer_raw,
    f.plan_raw,
    f.compare_amount,
    f.negotiated_dollar,
    f.negotiated_percentage,
    f.gross_charge,
    f.quality_flag,
    f.quality_notes,
    concat_ws('|', s.code, coalesce(f.setting, 'unknown'), f.pricing_method) AS comparison_group,
    median(f.compare_amount) OVER w AS group_median,
    count(*) OVER w AS group_rates,
    count(DISTINCT h.hospital_id) OVER w AS group_hospitals
FROM fact_rate f
JOIN dim_service s USING (service_id)
JOIN dim_hospital h USING (hospital_id)
JOIN dim_plan pl USING (plan_id)
JOIN dim_payer p ON p.payer_id = pl.payer_id
WHERE f.compare_amount IS NOT NULL
WINDOW w AS (PARTITION BY s.code, coalesce(f.setting, 'unknown'), f.pricing_method);

-- Per-code summary: one row per comparison group.
CREATE OR REPLACE VIEW v_compare_summary AS
SELECT code, service, setting, pricing_method, compare_unit,
       count(*) AS rates,
       count(DISTINCT hospital_id) AS hospitals,
       count(DISTINCT payer_name) AS payers,
       min(compare_amount) AS min_amount,
       median(compare_amount) AS median_amount,
       max(compare_amount) AS max_amount,
       count(DISTINCT pricing_method) AS methods_in_group,
       count(DISTINCT compare_unit) AS units_in_group
FROM v_rate_compare
GROUP BY ALL;
