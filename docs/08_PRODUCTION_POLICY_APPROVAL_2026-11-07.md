# Production handoff policy — limited approval record (7 November 2026)

Decision maker: **project owner**, who accepted the assistant's explicitly proposed conservative policy and 15-predictor allowlist in the project conversation. This records that decision; it is not an approval by the course instructor or an independent external auditor.

Approved for this **course prototype and pinned source workbook**:

- Keep all 5,630 original rows and all 20 source columns in the Crew 1 CSV; record SHA-256 hashes. Do not silently drop rows, impute, clip numeric values, rename columns, or merge category aliases.
- Permit nulls in the seven columns actually containing nulls. Refuse null IDs and target labels. `CustomerID` must be unique; `Churn` must be only 0 or 1. `Complain` is binary and `SatisfactionScore` must be an integer in 1–5.
- Pin the source-observed string category labels **without treating similar labels as equivalent**. Values not in this snapshot's fixed sets fail the validation gate rather than changing policy automatically.
- Preserve unknown units as unknown. Enforce only conservative, domain-justified nonnegative bounds on counters and similar measurements; do not infer upper bounds from observed maxima, and do not assume a currency.
- Approve exactly the 15 predictors in `contracts/production_policy_v1.json`. Exclude `CustomerID` (identifier), `Churn` (target), and, as a conservative prototype choice, `Gender`, `MaritalStatus`, `CityTier` from predictors. The last three **remain in the CSV for audit**, but do not become model inputs.
- Fit imputation and encoding only on model-training folds after the train/test split. Do not claim causal effects or a documented future churn prediction horizon.

**Boundary of approval:** This is an authored, version-controlled policy, *separate* from the generated Crew 1 contract. Approval alone never invokes Crew 2. `src/production_gate.py` must independently read the approved policy, the immutable candidate `dataset_contract.json`, and `clean_data.csv`, then return PASS with no failed rules. Failed validation must block training. A successful gate does **not** mean a model has been trained.

**Unresolved (not silently approved):** Actual field units where unknown, category synonym mappings, distance upper bounds, a forward observation/prediction horizon, and suitability outside this pinned dataset. The synthetic months-to-years semantic-error experiment remains separate and must not be misrepresented as a discovered unit error in the Kaggle workbook.

The submitted deadline is **17 November 2026** (superseding the earlier 10 November date).
