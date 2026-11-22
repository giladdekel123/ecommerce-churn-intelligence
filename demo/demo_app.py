"""Public, read-only portfolio demo: replay of two genuine, recorded pipeline runs.

Deliberately self-contained: it imports only the standard library and Streamlit,
reads only the sanitized JSON files in demo/evidence/, and has no code path that
launches a pipeline, calls Gemini, trains a model, accepts an upload or reads
secrets. The full operator application remains app.py.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import streamlit as st

import policy_lab

EVIDENCE = Path(__file__).resolve().parent / "evidence"
SCENARIOS = {"PASS — original dataset": "pass", "FAIL — corrupted workbook": "fail"}
ICON = {"COMPLETED": "✅ COMPLETED", "FAILED": "❌ FAILED", "BLOCKED": "⛔ BLOCKED", "PENDING": "· not yet reached"}
REPLAY_NOTICE = (
    "**Historical execution replay.** This page shows the recorded outcome of a genuine past run on the "
    "author's machine. No agents are running, no Gemini requests are made, nothing is uploaded or trained, "
    "and nothing here can start the pipeline. The one interactive exception is the *Interactive Validation Lab*, "
    "which checks a single value you choose against one approved rule; it is not a pipeline run."
)


@st.cache_data
def load_scenario(key: str) -> dict:
    """Load one evidence file, refusing it if it does not match the recorded SHA-256."""
    # Normalize line endings so a Windows (CRLF) checkout hashes the same as the LF original.
    raw = (EVIDENCE / f"{key}.json").read_bytes().replace(b"\r\n", b"\n")
    expected = json.loads((EVIDENCE / "MANIFEST.json").read_text(encoding="utf-8"))["sha256"][f"{key}.json"]
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(f"Evidence file {key}.json does not match MANIFEST.json")
    return json.loads(raw.decode("utf-8"))


def short_time(value: str | None) -> str:
    return value[11:19] + " UTC" if value else "—"


def overview(d: dict) -> None:
    m, g = d["manifest"], d["gate"]
    st.subheader(d["title"])
    st.caption(f"Recorded run `{d['run_id']}` · policy {g['policy_version']} · "
               f"{m['started_at'][:10]} · source SHA-256 `{d['source_sha256_short']}…`")
    st.write(d["source_description"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Independent gate", g["status"])
    c2.metric("Crew 2 reached", "Yes" if m["crew2_reached"] else "No")
    c3.metric("Model training", "Started" if m["model_training_started"] else "Not started")
    c4.metric("Model deployed", "No" if not m["model_deployed"] else "Unexpected")
    if g["status"] == "PASS":
        st.success("The deterministic gate authorized Crew 2. The run still needs human review; "
                   "it is a retrospective course experiment, not a production predictor.")
    else:
        st.error("The deterministic gate **stopped the run** before any model was trained. "
                 "The three Crew 2 agents never started.")
        st.info("Crew 1's agent reviews do not mention the corrupted value. It was the deterministic, "
                "independent gate, not an agent, that caught it. That separation is the point of the design.")


def pipeline(d: dict) -> None:
    st.markdown("Step through the **seven recorded component statuses** (six agents plus the gate).")
    comps = d["components"]
    step = st.slider("Replay position", 0, len(comps), len(comps), key=f"step_{d['scenario']}",
                     help="0 = before the run; 7 = the recorded end state. This is a replay control, not a live view.")
    rows = []
    for i, c in enumerate(comps):
        shown = i < step
        rows.append({"#": i + 1, "Component": c["name"], "Crew": c["crew"],
                     "Recorded status": ICON.get(c["state"], c["state"]) if shown else ICON["PENDING"],
                     "Started": short_time(c["started_at"]) if shown else "—",
                     "Finished": short_time(c["finished_at"]) if shown else "—"})
    st.dataframe(rows, hide_index=True, width="stretch")
    choice = st.selectbox("Inspect a component", [c["name"] for c in comps], key=f"comp_{d['scenario']}")
    c = next(c for c in comps if c["name"] == choice)
    st.markdown(f"**{c['name']}** · {c['crew']} · {c['kind']} · recorded state **{c['state']}**")
    if c["detail"]:
        st.write(f"Recorded detail: {c['detail']}")
    related = [e for e in d["excerpts"] if e["component"] == c["name"]]
    if related:
        st.caption(f"{len(related)} recorded report excerpt(s) are available in the *Agent excerpts* tab.")
    elif c["kind"] == "agent":
        st.caption("No report excerpt exists for this component: it never started in this run.")


def gate(d: dict) -> None:
    g = d["gate"]
    st.markdown(f"Source integrity (SHA-256 receipt): **{g['source_integrity_status']}**")
    if g["status"] == "PASS":
        st.success(f"Gate PASS under approved policy {g['policy_version']}: no rule violations. "
                   "Crew 2 was authorized.")
        return
    st.error(f"Gate FAIL under approved policy {g['policy_version']}. Crew 2 not authorized.")
    st.dataframe([{"Rule": f["rule_id"], "Column": f["column"], "Observed value": f["observed"],
                   "Approved limit": f"{f['approved_label']}: {f['approved_value']}",
                   "CSV line": f["csv_lines"], "Message": f["message"]} for f in g["findings"]],
                 hide_index=True, width="stretch")
    f = g["findings"][0]
    st.markdown(f"**Reading the finding.** A single `{f['column']}` value of `{f['observed']}` exceeds the "
                f"approved maximum of `{f['approved_value']}`. The approved limit comes from the "
                "version-controlled policy, never from the run's own contract, so a run cannot relax it. "
                "The gate fails closed.")


def model(d: dict) -> None:
    m = d["metrics"]
    if not m:
        st.warning("No model was trained in this run, so there are no metrics. Metrics from other runs are "
                   "deliberately not shown here.")
        return
    st.warning("**Retrospective experimental results, not evidence of production readiness.** Churn labels "
               "have no documented future prediction horizon; these are descriptive measurements on one "
               "historical dataset.")
    st.caption(f"{m['total_rows']:,} rows · {m['training_rows']:,} training / {m['test_rows']:,} held-out "
               f"({m['test_churn_count']} churners in held-out set). {m['selection_rule']}. {m['test_use']}.")
    h = m["holdout_once"]
    names = {"majority_baseline": "Majority baseline", "logistic_regression": "Logistic regression",
             "random_forest": "Random forest"}
    st.dataframe([{"Model": names[k] + (" (selected)" if k == m["winner"] else ""),
                   "Accuracy": v["accuracy"], "Precision": v["precision"], "Recall": v["recall"],
                   "F1": v["f1"], "ROC AUC": v["roc_auc"]} for k, v in h.items()],
                 hide_index=True, width="stretch")
    pick = st.radio("Confusion matrix (held-out set)", list(names), format_func=names.get, horizontal=True,
                    index=list(names).index(m["winner"]), key="cm_model")
    (tn, fp), (fn, tp) = h[pick]["confusion_matrix"]
    st.dataframe([{"": "Actual: retained", "Predicted: retained": tn, "Predicted: churn": fp},
                  {"": "Actual: churned", "Predicted: retained": fn, "Predicted: churn": tp}],
                 hide_index=True, width="content")
    st.markdown("**Accuracy alone misleads here:** the baseline that never predicts churn scores "
                f"{h['majority_baseline']['accuracy']:.1%} accuracy with zero recall.")
    cv = m["cv_training_only"]
    st.caption("Model selection used training-only 3-fold CV F1: " + "; ".join(
        f"{names[k]} {v['f1_mean']:.3f} ± {v['f1_std']:.3f}" for k, v in cv.items()))
    with st.expander("Recorded limitations"):
        for item in m["limitations"]:
            st.markdown(f"- {item}")


def excerpts(d: dict) -> None:
    st.caption("Short verbatim excerpts selected by the author from the agents' recorded reports. Full reports "
               "are not published. Agent output requires human review.")
    items = d["excerpts"]
    agents = list(dict.fromkeys(e["component"] for e in items))
    who = st.selectbox("Agent", agents, key=f"agent_{d['scenario']}")
    for e in items:
        if e["component"] == who:
            st.markdown(f"> {e['text']}")
            st.caption(f"Excerpt from `{e['source_report']}` · recorded run `{d['run_id']}`")
    if d["scenario"] == "fail":
        st.info("The Crew 2 agents (Feature Engineering Specialist, Machine Learning Engineer, Model "
                "Evaluation Specialist) were BLOCKED in this run, so they have no excerpts.")


def guided_review(d: dict) -> None:
    st.info("**Guided Review of recorded evidence.** This walks through what the original run recorded. It is not "
            "a live or simulated execution: only the recorded states and timestamps are shown, and no durations, "
            "intermediate states or agent actions are inferred.")
    steps = ["1 · Crew 1 agents", "2 · Independent gate", "3 · Crew 2 agents", "4 · Training implications"]
    step = st.radio("Review step", steps, horizontal=True, key=f"guided_{d['scenario']}")
    comps, m, g = d["components"], d["manifest"], d["gate"]

    def table(crew: str) -> None:
        st.dataframe([{"Component": c["name"], "Recorded status": ICON.get(c["state"], c["state"]),
                       "Recorded start": short_time(c["started_at"]), "Recorded finish": short_time(c["finished_at"]),
                       "Recorded detail": c["detail"] or "—"} for c in comps if c["crew"] == crew],
                     hide_index=True, width="stretch")

    if step == steps[0]:
        table("crew1")
        st.markdown("All three Crew 1 agents completed and wrote their reviews. **Their reviews do not authorize "
                    "anything:** authorization is decided only by the independent gate in the next step.")
    elif step == steps[1]:
        table("gate")
        st.markdown(f"Recorded decision: **{g['status']}** under approved policy {g['policy_version']} · source "
                    f"integrity {g['source_integrity_status']} · Crew 2 authorized: "
                    f"**{'yes' if g['authorizes_crew2_training'] else 'no'}**.")
        for f in g["findings"]:
            st.error(f"{f['rule_id']} on {f['column']}: observed {f['observed']}, "
                     f"{f['approved_label'].lower()} {f['approved_value']} (CSV line {f['csv_lines']}).")
    elif step == steps[2]:
        table("crew2")
        if m["crew2_reached"]:
            st.markdown("The gate authorized Crew 2 and all three Crew 2 agents completed.")
        else:
            st.markdown("The gate did not authorize Crew 2, so none of its three agents started. Each is recorded "
                        "as **BLOCKED**, not as failed or skipped silently.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Crew 2 reached", "Yes" if m["crew2_reached"] else "No")
        c2.metric("Model training", "Started" if m["model_training_started"] else "Not started")
        c3.metric("Model deployed", "No" if not m["model_deployed"] else "Unexpected")
        if m["crew2_reached"]:
            st.success("Models were trained and evaluated on a held-out set (see *Model results*). The run still "
                       "needs human review and nothing was deployed.")
        else:
            st.error("No model was trained, so this run has no metrics, and no other run's model is shown in its place.")


def validation_lab(_d: dict) -> None:
    st.subheader("Interactive Validation Lab")
    st.warning("**One value, one rule: this is not the dataset gate.** A PASS below means this single "
               "`SatisfactionScore` value satisfies this single rule. It does **not** mean a dataset has passed the "
               "independent gate.")
    try:
        policy = policy_lab.load_policy()
    except (OSError, KeyError, ValueError, TypeError, AttributeError):
        st.error("The approved policy file is missing or unusable, so the lab is disabled. "
                 "No fallback rule is used.")
        return
    spec = policy["spec"]
    if policy_lab.unsupported_constraints(spec):
        st.error("The approved policy now defines constraints this lab does not evaluate "
                 f"({', '.join(policy_lab.unsupported_constraints(spec))}). The lab refuses to give a verdict.")
        return
    default = policy_lab.default_value(spec)
    if "lab_score" not in st.session_state:
        st.session_state["lab_score"] = default

    def reset() -> None:
        st.session_state["lab_score"] = default

    value = st.slider("Candidate SatisfactionScore", 1, 10, key="lab_score")
    st.button("Reset to valid default", on_click=reset, key="lab_reset")
    result = policy_lab.evaluate(value, spec)
    lo, hi = spec["min"], spec["max"]
    st.caption(f"Approved range read from policy {policy['policy_version']}: {lo} to {hi} inclusive.")
    if result["status"] == "PASS":
        st.success(f"**Single-rule PASS.** {value} is within the approved range {lo}–{hi}. "
                   "This does not mean any dataset passed the gate.")
    else:
        word = "maximum" if result["bound"] == "max" else "minimum"
        cmp = "exceeds" if result["bound"] == "max" else "is below"
        st.error(f"**FAIL · {result['rule_id']}.** {value} {cmp} the approved {word} of {result['limit']}. "
                 "In the real gate this is a numeric-bound violation that stops the run before Crew 2.")
    fail = load_scenario("fail")["gate"]["findings"][0]
    st.markdown(f"The recorded FAIL scenario failed on exactly this rule: one `{fail['column']}` value of "
                f"`{fail['observed']}` on CSV line {fail['csv_lines']} (`{fail['rule_id']}`).")
    with st.expander("What the independent gate checks beyond this one value"):
        st.markdown("- the policy's approval status and that the contract matches it and cannot weaken it\n"
                    "- the run's source-integrity receipt (SHA-256 of the uploaded workbook)\n"
                    "- **every row** of the prepared CSV against **every column's** rules (type, missing values, "
                    "allowed values, numeric bounds)\n"
                    "- row, column and missing-cell counts and the CSV hash against the contract")
    st.caption("How this works: the limits are read from the version-controlled policy file at run time, not "
               "typed into the demo. The comparison mirrors the production gate's numeric-bound check, and "
               "automated tests run the same values through the real gate to confirm they agree. "
               "Nothing is sent anywhere or saved.")


def about() -> None:
    st.markdown("""
**What this is.** A two-crew CrewAI workflow for analysing historical e-commerce churn, with a deterministic
Python validation gate between the crews. Crew 2 may train models only if the data and contract pass the
approved policy.

**What this demo is not.** It is not the running application. It replays two genuine, recorded local runs
from sanitized evidence files. Nothing executes here.

**Limitations**
- Two recorded runs only; the scenario, review and replay controls navigate recorded evidence. The Interactive Validation Lab evaluates one value against one rule and is not a dataset-level check.
- Agent text is AI-generated and was reviewed only for excerpt selection.
- Model metrics are retrospective on a public dataset; no future horizon is defined and no deployment is claimed.
- No customer rows, models, logs or credentials are published.

**Data attribution.** Dataset: Ankit Verma, *Ecommerce Customer Churn Analysis and Prediction*
([Kaggle](https://www.kaggle.com/datasets/ankitverma2010/ecommerce-customer-churn-analysis-and-prediction)),
licensed [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/). This is a **non-commercial**
portfolio demo; share-alike conditions apply to adapted material. **Transformations:** the figures shown are
aggregate statistics, gate results and model metrics derived from the data; the FAIL scenario used a locally
modified copy of the workbook with one value altered. No dataset rows or workbooks are redistributed.
""")


def main() -> None:
    st.set_page_config(page_title="Churn Intelligence — Replay Demo", page_icon="🔍", layout="wide")
    st.title("E-commerce Churn Intelligence — Architecture V2")
    st.warning(REPLAY_NOTICE)
    label = st.radio("Recorded scenario", list(SCENARIOS), horizontal=True, key="scenario")
    d = load_scenario(SCENARIOS[label])
    tabs = st.tabs(["Overview", "Guided Review", "Seven recorded statuses", "Gate findings",
                    "Interactive Validation Lab", "Model results", "Agent excerpts", "About & limitations"])
    for tab, render in zip(tabs, (overview, guided_review, pipeline, gate, validation_lab, model, excerpts)):
        with tab:
            render(d)
    with tabs[7]:
        about()


main()
