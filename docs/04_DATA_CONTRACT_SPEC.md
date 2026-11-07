# 04 — Dataset Contract and Validation Specification

**Status:** The initial test policy (`contracts/approved_policy.json`, version `0.1.0-test`) and the deterministic Python validator (`src/contract_validator.py`) are implemented. Two structural automated tests have succeeded: a valid synthetic handoff returns PASS, and `Churn=7` returns FAIL. The complete production contract and the semantic validator are still pending.  
**Core instructor output:** `dataset_contract.json` published by Crew 1.

## 1. Why two related specifications?

Crew 1 publishes a contract describing its cleaned dataset. However, if Crew 1 were permitted to rewrite the rules whenever it accidentally changed a column, a dataset and a new contract could agree while both violated the intended interface.

**Proposed safeguard:** the repository contains a separate **author-approved policy**, maintained by ordinary code review; the gate validates both the generated contract and dataset against that policy. Crew 2 still receives only the generated contract and validated data as its handoff—not Crew 1's raw files or internal process.

A contract cannot prove that every factual value is true. It enforces **specified** rules. Unit-change detection on the Kaggle workbook is possible only where a trustworthy unit expectation and a testable check have been established. The semantic demonstration in section 6 uses a separate synthetic fixture with an explicitly defined unit. It does not assign a unit to a source column.

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
6. Demonstrate at least one semantic error, as required by Jonathan on 4 November 2026. The synthetic months-to-years experiment below is our proposed way to meet that requirement.

**Test harness:** test damage is injected between Crew 1 and gate, or a gate-only mode reads a controlled saved snapshot. Do not silently rerun Crew 1 and heal the corrupted artifact before testing it.

**Unit-change story (instructor illustration, not a workbook rule):** the fictional `order_value` cents-to-dollars scenario illustrates why contracts need unit assertions. Our chosen workbook does **not** have an `order_value` column, and it does not define the currency for `CashbackAmount`. A claimed dollar-vs-cent demonstration on `CashbackAmount` would invent a unit. `Tenure` in `E Comm` is also not assumed to be months; section 3 leaves its unit unspecified.

### 6.1 Semantic-error demonstration — proposed synthetic experiment

**Instructor requirement (Jonathan, 4 November 2026).** The project must include at least one semantic-error demonstration in which Crew 2 stops or a warning is raised. Jonathan required that demonstration. He did not prescribe the column, the unit, the reference dataset, or the comparison rule, and he did not require a hard stop in every case.

**Proposed implementation (DESIGN).** Our stricter choice is that a semantic validation failure blocks Crew 2. A warning alone does not satisfy this design. Meet the demonstration with a small synthetic months-to-years fixture. This fixture is not a profile of the Kaggle workbook and is not a substitute for the `Churn=7` test.

| Piece | Role |
|---|---|
| Frozen reference | A version-controlled synthetic dataset whose approved policy explicitly defines one numeric column, `SyntheticTenureMonths`, as **months**. The file and its content hash are the trusted reference. |
| Corrupted handoff | A frozen copy of that reference in which each `SyntheticTenureMonths` value has been divided by 12. The generated contract still declares **months** and does not record an approved months-to-years conversion. |
| What still looks valid | Both files use a numeric type. Both lie inside the preapproved permitted range of 0–120 months, which contains 12, 24, 36 and 1, 2, 3. |
| What the gate checks | Declared unit, the permitted range 0–120 months, and the mean and median of `SyntheticTenureMonths` against the frozen reference. |
| Blocking result | The declared unit still matches, and both value sets remain inside 0–120 months. The mean and median move from 24 to 2, a relative change greater than 50%. That unexplained shift is a semantic **FAIL**. Crew 2 is not invoked, and the failure report sets `model_training_started=false`. |

**Preapproved demonstration parameters (DESIGN).** These values and this threshold apply only to the synthetic demonstration. They are not measurements, units, or limits for `Tenure` or any other column in the Kaggle workbook, and they are not part of the production contract.

| Parameter | Preapproved value |
|---|---|
| Reference unit | months |
| Trusted reference values | 12, 24, 36 |
| Corrupted handoff values | 1, 2, 3 |
| Permitted numeric range | 0–120 months |
| Reference mean and median | 24 |
| Corrupted mean and median | 2 |
| Blocking threshold | Both the mean and the median differ from the trusted reference by more than 50% in relative terms |
| Expected semantic result | **FAIL**, blocking Crew 2 |

Rules for this experiment:

- The trusted reference is this approved fixture only. An arbitrary earlier run, including a previous successful model run, is not a reference.
- The reference unit is defined by the fixture's approved policy. It is not inferred from `Data Dict` or from the real `Tenure` column.
- Deterministic Python computes the mean and median from the reference file and from the handoff. The gate does not accept an LLM statement of the shift.
- The blocking threshold is preapproved: both the mean and the median must differ from the trusted reference by more than 50% in relative terms. Dividing 12, 24, and 36 by 12 is the intended breach. The tolerance is not tuned after seeing a pipeline run.
- "Unexplained" means the contract still declares months and lists no approved transformation that would account for dividing by 12.
- The gate reports the failed semantic rule, the column, the reference and handoff mean and median, and the declared unit. It does not repair the values.
- The synthetic fixture may be committed because it is small and invented. It must not be a copy or subset of the Kaggle workbook.

## 7. Modeling preprocessing boundary

Crew 1 may apply transformations that do not estimate information from future test observations (e.g., column name mapping, validated category standardization) and must document them. Crew 2 performs stratified train/test splitting, then **fits** imputation, scaling and encoding solely on training folds, using a scikit-learn Pipeline. The saved `.joblib` should include trained preprocessing so the model can be reproduced safely.
