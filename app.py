"""Local Streamlit dashboard for the e-commerce churn course project.

Run from the project root: python -m streamlit run app.py
The UI is a reviewer and launcher, not a model deployment service.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import os
import pandas as pd
import streamlit as st

from src.dashboard_data import list_runs, snapshot, read_report, eda_html

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
MAX_LOG_DISPLAY = 5000


def run_command(args: list[str], timeout: int) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run([sys.executable, *args], cwd=ROOT,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, check=False,env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    except subprocess.TimeoutExpired:
        st.error("Execution exceeded the safety time limit. Check the newest run manifest before starting anything else.")
    except OSError as exc:
        st.error(f"Could not launch Python: {exc}")
    return None


def display_result(output: subprocess.CompletedProcess[str] | None, *, label: str) -> None:
    if output is None:
        return
    if output.returncode == 0:
        st.success(f"{label}: command finished successfully")
    else:
        st.error(f"{label}: command exited with code {output.returncode}")
    with st.expander(f"{label} — terminal details (last {MAX_LOG_DISPLAY} characters)"):
        st.code((output.stdout + "\n" + output.stderr)[-MAX_LOG_DISPLAY:], language="text")


def report_view(run: dict, name: str, heading: str) -> None:
    content = read_report(run, name)
    if content:
        with st.expander(heading):
            st.markdown(content)
    else:
        st.caption(f"{heading}: not available for the selected run")


st.set_page_config(page_title="Churn Intelligence — Course Project", page_icon="📊", layout="wide")
st.title("E-commerce Churn Intelligence")
st.caption("Research prototype · Two CrewAI crews · Independent data contract gate · Not deployed")

with st.sidebar:
    st.header("Run controls")
    if st.button("Check readiness", help="No model calls or training"):
        with st.spinner("Checking local prerequisites..."):
            outcome = run_command(["src/full_pipeline.py", "--check"], timeout=40)
        display_result(outcome, label="Preflight")

    run_permission = st.checkbox("I understand that a fresh run calls Gemini and trains models")
    if st.button("Run complete pipeline", disabled=not run_permission, type="primary"):
        previous = set(list_runs(RUNS))
        with st.spinner("Executing a new isolated run. The interface will update when it finishes..."):
            outcome = run_command(["src/full_pipeline.py", "--run"], timeout=1200)
        st.session_state["latest_launch_result"] = {
            "code": outcome.returncode if outcome else None,
            "output": ((outcome.stdout + "\n" + outcome.stderr)[-MAX_LOG_DISPLAY:]
                       if outcome else "The command did not finish."),
        }
        newly_created = sorted(set(list_runs(RUNS)) - previous, reverse=True)
        if newly_created:
            st.session_state["selected_run"] = newly_created[0]
        st.rerun()

    ids = list_runs(RUNS)
    if not ids:
        selected = None
        st.info("No full-run manifests found yet. Run the pipeline to generate one.")
    else:
        current = st.session_state.get("selected_run")
        selected = st.selectbox("View saved run", ids, index=ids.index(current) if current in ids else 0)
        st.session_state["selected_run"] = selected
    st.caption("Each run is stored separately. Selecting a past run does not rerun or overwrite anything.")

launch = st.session_state.get("latest_launch_result")
if launch:
    if launch["code"] == 0:
        st.success("The last pipeline command completed. Review its run status and reports below.")
    else:
        st.warning("The last pipeline command was not confirmed successful. Review the selected run's status.")
    with st.expander("Last launch log (recent output only)"):
        st.code(launch["output"], language="text")

if selected is None:
    st.stop()

try:
    run = snapshot(RUNS, selected)
except ValueError as exc:
    st.error(str(exc))
    st.stop()

manifest = run["manifest"]
st.subheader(f"Selected run: {selected}")
status = manifest.get("status", "UNKNOWN")
st.caption(f"Saved-run status: {status}. This is a retrospective course experiment, not a live deployed predictor.")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Gate", manifest.get("validation_status", "NOT_RUN"))
col2.metric("Crew 2 reached", "Yes" if manifest.get("crew2_reached") else "No")
col3.metric("Training started", "Yes" if manifest.get("model_training_started") else "No")
col4.metric("Model deployed", "No" if not manifest.get("model_deployed") else "Unexpected — review")
if status.startswith("FAILED"):
    st.error("This run stopped before successful completion. No model results from other runs will be shown as its results.")
if status == "FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW":
    st.info("Completed automatically; still requires human review. Predictions are not validated for future operational use.")

overview, eda_tab, model_tab, gate_tab = st.tabs([
    "Overview", "Crew 1 · Data & EDA", "Crew 2 · Models & reviews", "Gate · Failure demonstration"
])

with overview:
    st.markdown("**Execution details**")
    st.json({key: manifest.get(key) for key in (
        "run_id", "status", "stage", "started_at", "updated_at", "validation_status",
        "failed_rule_ids", "crew2_reached", "model_training_started", "model_deployed", "error"
    )})
    if run["eda"]:
        summary = run["eda"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Customer records", f"{summary.get('customers', 0):,}")
        c2.metric("Historical churn rate", f"{100 * summary.get('historical_churn_rate', 0):.1f}%")
        c3.metric("Missing cells retained", f"{summary.get('missing_cells', 0):,}")
    st.caption("The source data and model binary are kept local and are not offered for download.")

with eda_tab:
    if run["eda"]:
        s = run["eda"]
        counts = s.get("churn_counts", {})
        st.markdown("**Recorded churn distribution**")
        st.bar_chart(pd.DataFrame({"customers": [counts.get("0", 0), counts.get("1", 0)]},
                                  index=["Non-churn", "Churn"]))
        devices = s.get("preferred_login_device_by_churn", [])
        if devices:
            st.markdown("**Historical churn rate by reported login device**")
            df = pd.DataFrame(devices)
            st.bar_chart(df.set_index("category")[["historical_churn_rate"]])
            st.caption("Observed categories remain unmerged; differences do not establish causality.")
        html = eda_html(run)
        if html:
            st.download_button("Open full EDA HTML report", html,
                               file_name=f"{selected}_eda_report.html", mime="text/html")
    else:
        st.info("No Crew 1 EDA summary for this run")
    report_view(run, "quality_review", "Data quality agent")
    report_view(run, "eda_interpretation", "EDA agent")
    report_view(run, "insights", "Business insights agent")

with model_tab:
    metrics = run["metrics"]
    if metrics:
        st.success(f"Selected model: {metrics.get('winner', 'Unknown')}")
        holdout = metrics.get("holdout_once", {})
        if holdout:
            fields = ("accuracy", "precision", "recall", "f1", "roc_auc")
            rows = {name: {key: round(100 * values[key], 1) for key in fields if key in values}
                    for name, values in holdout.items()}
            st.dataframe(pd.DataFrame.from_dict(rows, orient="index"), width="stretch")
            st.caption("All scores are percentages; selected by train-only CV. Held-out evaluation does not establish future predictive validity.")
            winner = holdout.get(metrics.get("winner"), {})
            cm = winner.get("confusion_matrix")
            if isinstance(cm, list) and len(cm) == 2:
                st.write("**Selected model — held-out confusion matrix**")
                st.dataframe(pd.DataFrame(cm, columns=["Predicted non-churn", "Predicted churn"],
                                          index=["Actual non-churn", "Actual churn"]))
        st.warning("Churn outcome has no documented future prediction horizon. Do not deploy or interpret these values as prospective guarantees.")
    else:
        st.info("No verified completed-model metrics exist for this selected run")
    for key, label in (
        ("evaluation_report", "Canonical evaluation report"),
        ("model_card", "Canonical model card"),
        ("feature_engineering_review", "Feature Engineering Specialist"),
        ("model_selection_review", "Machine Learning Engineer"),
        ("evaluation_review", "Model Evaluation Specialist"),
    ):
        report_view(run, key, label)

with gate_tab:
    gate = run["gate"]
    if gate:
        st.markdown("**Independent validation report for this run**")
        st.json({key: gate.get(key) for key in (
            "status", "authorizes_crew2_training", "model_training_started", "failed_rules"
        )})
    st.markdown("**Deliberately corrupt a temporary copy of the selected run's data**")
    st.caption("Runs a gate-only demonstration. It never modifies the original run, retrains a model, or invokes Gemini.")
    if st.button("Demonstrate validation failure"):
        with st.spinner("Running isolated negative test..."):
            output = run_command(["src/integration_flow.py", "--run-dir", str(run["dir"]), "--demo-fail"],
                                 timeout=90)
        if output:
            body = output.stdout
            if '"status": "FAILED_VALIDATION"' in body and '"crew2_reached": false' in body:
                st.success("Expected result: invalid data blocked and Crew 2 NOT reached.")
            else:
                st.error("The expected failure was not confirmed; inspect the output.")
        display_result(output, label="Negative validation demo")
