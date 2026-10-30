# 04 — Dataset Contract and Validation Specification

**Status:** Contract design baseline. Exact approved policy and code remain to be implemented.  
**Core instructor output:** `dataset_contract.json` published by Crew 1.

## 1. Why two related specifications?

Crew 1 publishes a contract describing its cleaned dataset. However, if Crew 1 were permitted to rewrite the rules whenever it accidentally changed a column, a dataset and a new contract could agree while both violated the intended interface.

**Proposed safeguard:** the repository contains a separate **author-approved policy**, maintained by ordinary code review; the gate validates both the generated contract and dataset against that policy. Crew 2 still receives only the generated contract and validated data as its handoff—not Crew 1's raw files or internal process.

A contract cannot prove that every factual value is true. It enforces **specified** rules. Unit-change detection is possible only where a trustworthy unit expectation and a testable check have been established.

## 2. Verified source profile

- Workbook `E Commerce Dataset.xlsx` → worksheet `E Comm`.
- 5,630 customer records × 20 columns, unique `CustomerID` values.
- Outcome counts: `Churn=0` = 4,682; `Churn=1` = 948.
- Total missing cells = 1,856, distributed as: `Tenure` 264; `WarehouseToHome` 251; `HourSpendOnApp` 255; `OrderAmountHikeFromlastYear` 265; `CouponUsed` 256; `OrderCount` 258; `DaySinceLastOrder` 307.
- Observed category aliases needing a reviewed rule: `Phone` / `Mobile Phone`; `CC` / `Credit Card`; `COD` / `Cash on Delivery`; `Mobile` / `Mobile Phone` within order categories may require domain confirmation.
- Actual source field spellings: `PreferedOrderCat` and `OrderAmountHikeFromlastYear` (do not silently correct names across crew boundary).
- `Data Dict` defines `Churn` only as **Churn Flag**. It does not establish a specific future prediction horizon, and some measurement units are unspecified.

## 3. Candidate policy constraints — distinguish verified from proposed

| Field | Proposed rule | Status / rationale |
|---|---|---|
| `CustomerID` | Present, unique, non-null, excluded from model inputs | Supported by source identifier role |
| `Churn` | Present, non-null, permitted values `{0,1}`; target only | Supported by observed source and problem definition |
| `Complain` | Permitted values `{0,1}`, non-null | Supported by observed source and dictionary |
| `SatisfactionScore` | Integer from 1 through 5 | Supported by source values, semantic interpretation to be approved |
| `CityTier` | Values `{1,2,3}` | Observed categories; future data policy should be confirmed |
| `OrderCount`, `CouponUsed`, `DaySinceLastOrder` | Nonnegative if present; nullable according to explicit policy | Semantically plausible, source includes nulls |
| `Tenure`, `WarehouseToHome`, `HourSpendOnApp` | Numeric; nonnegative if present; nullable by policy | Source measurements; units must not be invented |
| `CashbackAmount` | Numeric and nonnegative | Currency unspecified; **do not label USD** |
| Categorical fields | Allowed sets after **approved** standardization | Candidate category mappings need sign-off |
| Column set | Fixed minimum required feature/target fields and known canonical spelling | Complete actual schema to be pinned in policy |

The observed minima/maxima are profiling facts, **not automatically approved hard-coded limits**. A rule must be justified and maintained, not inferred to accommodate an incidental data value.

## 4. Contract document: proposed content

`dataset_contract.json` should contain, at minimum:

1. `contract_version` and `approved_policy_version`.
2. `source_id`, source-data hash and cleaning transformation version.
3. `columns`: names, data types, null policy, valid categories and numeric constraints.
4. `target_column`: `Churn`, with legal labels 0 and 1.
5. `model_input_columns` (explicit allowlist, excluding `CustomerID` and target).
6. `unit_assumptions`: accurate documented units or **unknown** where not established.
7. `known_limitations`: missing observation/churn horizon and other important assumptions.

The code-defined policy must validate the generated contract's structure; Pydantic or JSON Schema may validate the JSON shape. Separate deterministic checks validate **all rows** of `clean_data.csv`.

## 5. Validation and failure semantics

**PASS** only if every blocking rule holds and the generated contract is compatible with the approved policy. Otherwise **FAIL** with:

- failed rule ID; relevant column/row samples;
- expected vs observed values; contract and policy versions;
- `run_id`, timestamps, source/hash, stage status;
- plain-language explanation and explicit `model_training_started=false` if the gate failed.

Do not mutate/fix data in the gate. Do not invoke Crew 2 on FAIL. Keep the failed run isolated from previous successful outputs.

## 6. Required negative experiments

1. Remove mandatory `Churn` or another mandatory field from a **frozen Crew 1 artifact**.
2. Insert `Churn=7` into a sample copy.
3. Duplicate `CustomerID`.
4. Alter the generated contract to relax an approved constraint.
5. After one successful run, fail a second run and show that no stale model is counted as its result.

**Test harness:** test damage is injected between Crew 1 and gate, or a gate-only mode reads a controlled saved snapshot. Do not silently rerun Crew 1 and heal the corrupted artifact before testing it.

**Unit-change story:** the instructor's fictional `order_value` cents-to-dollars scenario illustrates why contracts need unit assertions. Our chosen workbook does **not** have an `order_value` column, and it does not define the currency for `CashbackAmount`. A claimed dollar-vs-cent demonstration would be unsupported without an additional explicitly governed unit-bearing test fixture.

## 7. Modeling preprocessing boundary

Crew 1 may apply transformations that do not estimate information from future test observations (e.g., column name mapping, validated category standardization) and must document them. Crew 2 performs stratified train/test splitting, then **fits** imputation, scaling and encoding solely on training folds, using a scikit-learn Pipeline. The saved `.joblib` should include trained preprocessing so the model can be reproduced safely.
