"""Local Streamlit dashboard for the e-commerce churn course project (Architecture V2).

Run from the project root: python -m streamlit run app.py
Upload an .xlsx workbook, acknowledge, then click Start Analysis. The pipeline runs
in a separate process (src/run_launcher.py); this page only displays what that
run records. The UI is a reviewer and launcher, not a model deployment service.

The page opens clean ("Not started"). It shows only the current analysis (the
active run, or the run started in this browser session). Historical runs appear
only after the user turns on "View Past Runs" and explicitly chooses one.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

from src import run_launcher
from src.dashboard_data import eda_html, gate_findings, list_runs, read_report, snapshot

ROOT = Path(__file__).resolve().parent
MAX_LOG_DISPLAY = 5000
REFRESH_SECONDS = 2.0
BADGES = {"PENDING": "⏳ Pending", "RUNNING": "🔄 Running", "COMPLETED": "✅ Completed",
          "FAILED": "❌ Failed", "BLOCKED": "⛔ Blocked", "NOT_APPLICABLE": "— Not applicable"}
ACTIVITIES = ("source_intake", "crew1_preparation", "contract_draft", "crew2_modeling")


def run_command(args: list[str], timeout: int) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run([sys.executable, *args], cwd=ROOT,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, check=False,
                              env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    except subprocess.TimeoutExpired:
        st.error("The readiness check exceeded its time limit.")
    except OSError as exc:
        st.error(f"Could not launch Python: {exc}")
    return None


def report_view(run: dict, name: str, heading: str) -> None:
    content = read_report(run, name)
    if content:
        with st.expander(heading):
            st.markdown(content)
    else:
        st.caption(f"{heading}: not available for the selected run")


def reset_analysis() -> None:
    """'Start a new analysis': forget this session's run and workbook; nothing is deleted."""
    for key in ("launched_run", "launched_upload", "staged", "consent"):
        st.session_state.pop(key, None)
    st.session_state["upload_generation"] = st.session_state.get("upload_generation", 0) + 1


def staged_upload(active: dict | None) -> dict | None:
    """Store an uploaded file once per upload. Uploading never starts processing."""
    generation = st.session_state.get("upload_generation", 0)
    uploaded = st.file_uploader("Upload Dataset — Excel workbook (.xlsx) containing an 'E Comm' worksheet",
                                type=["xlsx"], key=f"upload_{generation}")
    if uploaded is None:
        st.session_state.pop("staged", None)
        return None
    staged = st.session_state.get("staged")
    if not staged or staged.get("file_id") != uploaded.file_id:
        try:
            staged = run_launcher.stage_upload(uploaded.name, uploaded.getvalue())
        except run_launcher.UploadError as exc:
            staged = {"name": uploaded.name, "error": str(exc)}
        staged["file_id"] = uploaded.file_id
        st.session_state["staged"] = staged
        # A different workbook is a NEW pending analysis: it needs its own consent,
        # and a finished run from this session is no longer shown as current.
        # An ACTIVE run is still followed (it cannot be replaced while running).
        st.session_state["consent"] = False
        if not (active and active.get("alive")) and st.session_state.get("launched_upload") != uploaded.file_id:
            st.session_state.pop("launched_run", None)
            st.session_state.pop("launched_upload", None)
    return staged


def describe_run(state: dict) -> str:
    status = state.get("status") or "starting"
    return {"RUNNING": f"Running ({status})", "FINISHED": f"Finished: {status}",
            "INTERRUPTED": f"Interrupted (last status: {status})",
            "NOT_STARTED": "Did not start"}.get(state["state"], state["state"].title())


def launcher_panel(active: dict | None) -> None:
    st.subheader("New analysis")
    staged = staged_upload(active)
    launched = st.session_state.get("launched_run")
    state = run_launcher.run_state(launched) if launched else None
    # This workbook already has a run in this session (running or finished): never
    # relaunch it from the same upload. A launch that never started may be retried.
    analyzed = bool(staged and state and state["state"] != "NOT_STARTED"
                    and st.session_state.get("launched_upload") == staged.get("file_id"))
    if staged and staged.get("error"):
        st.error(f"Upload rejected: {staged['name']} — {staged['error']}")
    elif staged and analyzed:
        st.info(f"Workbook **{staged['name']}** · SHA-256 `{staged['sha256'][:16]}…` · Already analyzed in run "
                f"**{launched}** — {describe_run(state)}. Upload a different workbook, or click "
                "Start a new analysis, to run again.")
    elif staged:
        st.success(f"Uploaded: **{staged['name']}** ({staged['size'] / 1024:,.0f} KB) · "
                   f"SHA-256 `{staged['sha256'][:16]}…` · Not started")
    consent = st.checkbox("I understand that Start Analysis makes Gemini API calls and may train "
                          "machine-learning models on this workbook.", key="consent")
    running = bool(active and active.get("alive"))
    ready = bool(staged and not staged.get("error"))
    if running:
        st.info(f"Analysis {active.get('run_id') or '(starting)'} is running. A new analysis can start "
                "after it finishes.")
    left, right = st.columns([1, 3])
    with left:
        clicked = st.button("Start Analysis", type="primary", key="start",
                            disabled=running or not ready or not consent or analyzed)
    with right:
        if launched and not running:
            st.button("Start a new analysis", key="reset", on_click=reset_analysis,
                      help="Clears this page for a new workbook. Saved runs are kept (View Past Runs).")
    if clicked:
        try:
            info = run_launcher.launch(staged)
        except run_launcher.AlreadyRunning as exc:
            st.warning(str(exc))
        except (run_launcher.UploadError, OSError, KeyError) as exc:
            st.error(f"Could not start the analysis: {exc}")
        else:
            st.session_state["launched_run"] = info["run_id"]
            st.session_state["launched_upload"] = staged.get("file_id")
            st.rerun()


def component_card(name: str, entry: dict) -> None:
    st.markdown(f"**{name}**  \n{BADGES.get(entry.get('state'), entry.get('state'))}")
    detail = entry.get("detail")
    if detail:
        st.caption(detail)


def monitor_body(run_id: str, state: dict) -> None:
    live = state.get("live")
    status = state.get("status") or "starting"
    if state["state"] == "RUNNING":
        st.info(f"Run {run_id} is running ({status}). This view refreshes every "
                f"{REFRESH_SECONDS:.0f} seconds; no new run is started.")
    elif state["state"] == "FINISHED":
        (st.success if status == run_launcher.FINAL_SUCCESS else st.error)(
            f"Run {run_id} finished: **{status}**. This analysis is complete and will not be relaunched.")
    elif state["state"] == "INTERRUPTED":
        st.error(f"Run {run_id} was INTERRUPTED: its process ended before a final status was recorded "
                 f"(last status: {status}). This is not a completed run.")
    elif state["state"] == "NOT_STARTED":
        st.error(f"Run {run_id} did not start. The pipeline stopped before creating the run "
                 "(for example, a failed readiness check).")
        with st.expander("Launch log"):
            st.code(run_launcher.log_tail(run_id) or "(no output)", language="text")
        return
    if not live:
        st.caption("Waiting for the run to record its first status…")
        return
    comps = live.get("components", {})
    groups = (("Crew 1 · Data analysis", "crew1"), ("Independent gate", "gate"), ("Crew 2 · Modeling", "crew2"))
    for column, (title, crew) in zip(st.columns(3), groups):
        with column:
            st.markdown(f"##### {title}")
            for name, entry in comps.items():
                if entry.get("crew") == crew:
                    component_card(name, entry)
    stages = live.get("stages", {})
    lines = [f"- {stages[n]['label']}: {BADGES.get(stages[n]['state'], stages[n]['state'])}"
             for n in ACTIVITIES if n in stages]
    st.caption("Deterministic activities (Python code, not agents)")
    st.markdown("\n".join(lines))


def live_monitor(run_id: str) -> None:
    state = run_launcher.run_state(run_id)
    if state["state"] == "UNKNOWN" or (state["live"] is None and state["state"] == "FINISHED"):
        return  # e.g. a V1 historical run: nothing live to show
    st.subheader("Live execution monitor")
    was_running = state["state"] == "RUNNING"

    @st.fragment(run_every=REFRESH_SECONDS if was_running else None)
    def panel():
        current = run_launcher.run_state(run_id)
        monitor_body(run_id, current)
        if was_running and current["state"] != "RUNNING":
            st.rerun()  # run ended: refresh the whole page once, then stop polling

    panel()


def validation_outcome(run: dict) -> None:
    gate, manifest = run["gate"], run["manifest"]
    if not gate:
        st.caption("No independent validation report for this run yet.")
        return
    integrity = gate.get("source_integrity")
    if integrity:
        verified = integrity.get("status") == "VERIFIED"
        text = (f"Source integrity: **{integrity.get('status')}** · run-specific receipt SHA-256 "
                f"`{integrity.get('verified_source_sha256', '—')}`")
        (st.success if verified else st.error)(text)
    if gate.get("status") == "PASS":
        st.success(f"Independent gate: **PASS** (policy {gate.get('policy_version')}). Crew 2 authorized.")
    else:
        st.error(f"Independent gate: **FAILED_VALIDATION** (policy {gate.get('policy_version')}). "
                 "Crew 2 not authorized.")
        rows = gate_findings(gate)
        if rows:
            st.dataframe(pd.DataFrame([{
                "Rule": r["rule_id"], "Column": r["column"], "Observed value(s)": r["observed"],
                "Approved limit": f"{r['approved_label']}: {r['approved']}" if r["approved_label"] else "—",
                "CSV line(s)": r["rows"], "Message": r["message"]} for r in rows]), hide_index=True,
                width="stretch")
    c1, c2 = st.columns(2)
    c1.metric("Crew 2", "Reached" if manifest.get("crew2_reached") else "Blocked")
    c2.metric("Model training", "Started" if manifest.get("model_training_started") else "Not started")


def run_details(runs_root: Path, run_id: str) -> None:
    """Results of exactly one run. Never mixes in another run's data."""
    try:
        run = snapshot(runs_root, run_id)
    except ValueError:
        return  # the run has not written its manifest yet (or the ID is invalid)
    manifest = run["manifest"]
    st.divider()
    st.subheader(f"Run results: {run_id}")
    status = manifest.get("status", "UNKNOWN")
    if run_launcher.run_state(run_id, runs_root=runs_root)["state"] == "INTERRUPTED":
        st.error("This run was interrupted before recording a final status. It is not a completed run.")
    st.caption(f"Status: {status} · Pipeline: {manifest.get('pipeline_mode', 'V1_PINNED_SOURCE')} · "
               f"Policy {manifest.get('policy_version') or '1.0.0'}. Retrospective course experiment, not a "
               "deployed predictor.")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Gate", manifest.get("validation_status", "NOT_RUN"))
    col2.metric("Crew 2 reached", "Yes" if manifest.get("crew2_reached") else "No")
    col3.metric("Training started", "Yes" if manifest.get("model_training_started") else "No")
    col4.metric("Model deployed", "No" if not manifest.get("model_deployed") else "Unexpected — review")
    if status.startswith("FAILED"):
        st.error("This run stopped before successful completion. No model results from other runs are shown "
                 "as its results.")
    if status == "FULL_RUN_COMPLETE_NEEDS_HUMAN_REVIEW":
        st.info("Completed automatically; still requires human review. Predictions are not validated for "
                "future operational use.")

    overview, eda_tab, model_tab, gate_tab = st.tabs([
        "Overview", "Crew 1 · Data & EDA", "Crew 2 · Models & reviews", "Validation gate report"
    ])

    with overview:
        validation_outcome(run)
        st.markdown("**Execution details**")
        st.json({key: manifest.get(key) for key in (
            "run_id", "status", "stage", "started_at", "updated_at", "validation_status",
            "failed_rule_ids", "crew2_reached", "model_training_started", "model_deployed",
            "pipeline_mode", "policy_version", "source_file", "source_sha256", "error"
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
                                   file_name=f"{run_id}_eda_report.html", mime="text/html")
        else:
            st.info("No Crew 1 EDA summary for this run")
        report_view(run, "quality_review", "Data quality agent")
        report_view(run, "eda_interpretation", "EDA agent")
        report_view(run, "insights", "Business insights agent")

    with model_tab:
        metrics = run["metrics"]  # only ever loaded for this run's own verified completion
        if metrics:
            st.success(f"Selected model: {metrics.get('winner', 'Unknown')}")
            holdout = metrics.get("holdout_once", {})
            if holdout:
                fields = ("accuracy", "precision", "recall", "f1", "roc_auc")
                rows = {name: {key: round(100 * values[key], 1) for key in fields if key in values}
                        for name, values in holdout.items()}
                st.dataframe(pd.DataFrame.from_dict(rows, orient="index"), width="stretch")
                st.caption("All scores are percentages; selected by train-only CV. Held-out evaluation does "
                           "not establish future predictive validity.")
                winner = holdout.get(metrics.get("winner"), {})
                cm = winner.get("confusion_matrix")
                if isinstance(cm, list) and len(cm) == 2:
                    st.write("**Selected model — held-out confusion matrix**")
                    st.dataframe(pd.DataFrame(cm, columns=["Predicted non-churn", "Predicted churn"],
                                              index=["Actual non-churn", "Actual churn"]))
            st.warning("Churn outcome has no documented future prediction horizon. Do not deploy or "
                       "interpret these values as prospective guarantees.")
        else:
            st.info("No verified completed-model metrics exist for this run")
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
            st.markdown("**Independent validation report for this run (read-only)**")
            validation_outcome(run)
            st.json({key: gate.get(key) for key in (
                "status", "policy_version", "contract_version", "source_integrity",
                "authorizes_crew2_training", "model_training_started", "failed_rules"
            )})
        else:
            st.info("No independent validation report exists for this run.")


st.set_page_config(page_title="Churn Intelligence — Course Project", page_icon="📊", layout="wide")
st.title("E-commerce Churn Intelligence")
st.caption("Research prototype · Two CrewAI crews · Independent data contract gate · Not deployed")

runs_root = run_launcher.RUNS_ROOT
active = run_launcher.current_run(runs_root)

with st.sidebar:
    st.header("Run controls")
    if st.button("Check readiness", help="No model calls or training", key="readiness"):
        staged = st.session_state.get("staged")
        args = ["src/full_pipeline.py", "--check"]
        if staged and not staged.get("error"):
            args += ["--upload", staged["path"]]
        with st.spinner("Checking local prerequisites..."):
            outcome = run_command(args, timeout=60)
        if outcome is not None:
            (st.success if outcome.returncode == 0 else st.error)(
                "Ready for a fresh run" if outcome.returncode == 0 else "Not ready — see details")
            with st.expander("Readiness details"):
                st.code(outcome.stdout[-MAX_LOG_DISPLAY:], language="json")
    st.divider()
    view_past = st.toggle("View Past Runs", key="view_past",
                          help="Show a saved run's results. Nothing is rerun or overwritten.")
    past_run = None
    if view_past:
        ids = list_runs(runs_root)
        if ids:
            past_run = st.selectbox("Choose a past run", ids, index=None, placeholder="Select a run…",
                                    key="past_run")
        else:
            st.info("No saved runs yet.")
        st.caption("Each run is stored separately. Viewing a past run does not rerun or overwrite anything.")

if view_past:
    st.header("Past run")
    if past_run:
        live_monitor(past_run)
        run_details(runs_root, past_run)
    else:
        st.info("Choose a past run in the sidebar to view its results.")
    st.stop()

launcher_panel(active)

# The current analysis: an active run (reconnects after refresh / from any session),
# otherwise only the run started in THIS session. Never an older historical run.
current = ((active or {}).get("run_id") if active and active.get("alive") else None) \
    or st.session_state.get("launched_run")
if not current:
    st.info("Status: **Not started** — upload a dataset, confirm the acknowledgment and click Start Analysis.")
    st.stop()

live_monitor(current)
run_details(runs_root, current)
