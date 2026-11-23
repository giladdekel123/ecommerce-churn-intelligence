"""Protected live-execution page (Streamlit). Separate from the public replay demo (demo/demo_app.py).

Authentication is st.login (Google OIDC). This page forwards only the raw ID token; the worker VERIFIES it
(signature, issuer, audience, expiry) and AUTHORIZES the verified subject against its own allow-list, quotas and
kill switch. Nothing this page says about identity is trusted. This page has no upload widget, no Gemini import and no
secret: it can only ask the worker to start one of two predefined scenarios and to report on the caller's own run.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from live import web_client  # noqa: E402
from live.catalog import PUBLIC_SCENARIOS  # noqa: E402

POLL_SECONDS = 3
ACTIVE_LIMIT_STATES = ("RESERVED", "STARTED")
ERRORS = {
    "UNAVAILABLE": "Live execution is not available right now.",
    "DENIED": "You are not authorized to use live execution.",
    "UNAUTHENTICATED": "Your sign-in is not valid or has expired. Sign out and sign in again.",
    "BAD_REQUEST": "The request was not valid.",
    "INTERNAL": "Something went wrong on the server.",
    "TOO_LARGE": "The answer was too large to display.",
    "BAD_RESPONSE": "The server answered unexpectedly.",
}
STATE_ICON = {"COMPLETED": "✅", "RUNNING": "⏳", "PENDING": "⏸️", "FAILED": "❌", "BLOCKED": "⛔"}
LIMIT_TEXT = {"TIMED_OUT": "The run was stopped because it exceeded the execution time limit.",
              "KILLED": "The run was stopped by the emergency stop.",
              "INTERRUPTED": "The run was interrupted before it finished."}


def banner() -> None:
    st.error("**LIVE EXECUTION.** This page starts a real run of the agent pipeline on a fixed test dataset. "
             "It is not the historical replay shown on the public demo.", icon="🔴")


def render_metrics(metrics) -> None:
    if not metrics:
        return
    st.subheader("Held-out model results (retrospective, experimental)")
    st.caption(metrics.get("note", ""))
    rows = [{"model": name, **{k: v for k, v in values.items() if k != "confusion_matrix"}}
            for name, values in (metrics.get("holdout") or {}).items()]
    if rows:
        st.dataframe(rows, hide_index=True)
    st.write(f"Selected model: **{metrics.get('winner')}** ({metrics.get('selection_rule')})")


def render_view(view: dict) -> None:
    """Draw one sanitized projection. Only fields the worker allow-listed ever arrive here."""
    st.markdown(f"**Live run** `{view.get('run_id')}` · state **{view.get('run_state')}**"
                + (f" · limit state **{view['limit_state']}**" if view.get("limit_state") else ""))
    if view.get("limit_state") in LIMIT_TEXT:
        st.warning(LIMIT_TEXT[view["limit_state"]])
    st.subheader("Agents")
    for comp in view.get("components") or []:
        line = f"{STATE_ICON.get(comp['state'], '•')} **{comp['name']}** ({comp.get('crew')}): {comp['state']}"
        if comp.get("detail"):
            line += f" — {comp['detail']}"
        st.markdown(line)
    gate = view.get("gate")
    if gate:
        st.subheader("Independent validation gate")
        if gate["status"] == "PASS":
            st.success(f"Gate **PASS** (policy {gate.get('policy_version')}). Crew 2 may train models.")
        else:
            st.error(f"Gate **{gate['status']}** (policy {gate.get('policy_version')}). Crew 2 is blocked; "
                     "no model was trained.")
        for f in gate.get("findings") or []:
            st.markdown(f"- **{f['rule_id']}** `{f['column']}`: observed {f['observed']}; approved "
                        f"{f.get('approved_label')} {f.get('approved_value')} (CSV lines {f.get('csv_lines')}). "
                        f"{f['message']}")
    outcome = view.get("outcome") or {}
    if outcome:
        st.caption(f"Crew 2 reached: {outcome.get('crew2_reached')} · model training started: "
                   f"{outcome.get('model_training_started')} · model deployed: {outcome.get('model_deployed')}")
    reports = view.get("reports") or []
    if reports:
        st.subheader("Agent reports")
        for report in reports:
            with st.expander(f"{report['title']} — {report['component']}"):
                st.text(report["text"])  # plain text: agent output is never interpreted as markup or HTML
    render_metrics(view.get("metrics"))


def _fail(response: dict) -> None:
    st.warning(ERRORS.get(response.get("error"), "The request could not be completed."))


def monitor(client, run_id: str) -> None:
    response = client.status(web_client.current_id_token(), run_id)
    if not response.get("ok"):
        _fail(response)
        return
    render_view(response["view"])


def main() -> None:
    st.set_page_config(page_title="Live Analysis", layout="wide")
    st.title("Live Analysis (protected)")
    banner()
    present = web_client.forbidden_secrets_present()
    if present:  # defence in depth: this process must never be able to call Gemini
        st.error("This service is misconfigured and has stopped. A model credential is visible to the web process.")
        st.stop()
    if not web_client.is_logged_in():
        st.info("Sign in with Google to continue. Access is limited to approved accounts.")
        if st.button("Sign in with Google"):
            st.login()
        st.stop()
    if web_client.current_id_token() is None:
        st.error("Sign-in succeeded but no identity token is available (the auth setting expose_tokens = \"id\" "
                 "is required). Live execution stays off.")
        if st.button("Sign out"):
            st.logout()
        st.stop()
    client = web_client.get_client()
    usage = client.usage(web_client.current_id_token())
    if not usage.get("ok"):
        _fail(usage)
        if st.button("Sign out"):
            st.logout()
        st.stop()
    info = usage["usage"]
    st.caption(f"Starts today: {info['starts_today']} of {info['daily_limit']} · "
               f"budget used this month: ${info['month_charged_usd']:.2f} of ${info['monthly_budget_usd']:.2f}")
    if info.get("kill_switch_engaged"):
        st.warning("Live execution is currently switched off.")

    if "run_id" not in st.session_state:  # a refreshed or second tab re-attaches to the caller's latest run
        latest = client.latest(web_client.current_id_token())
        if latest.get("ok") and latest.get("latest"):
            st.session_state["run_id"] = latest["latest"]["run_id"]
    run_id = st.session_state.get("run_id")
    active = False
    if run_id:
        view = client.status(web_client.current_id_token(), run_id)
        active = bool(view.get("ok")) and (view["view"].get("limit_state") in ACTIVE_LIMIT_STATES
                                           or view["view"].get("run_state") == "RUNNING")
        if active:
            st.info("A live run is in progress. Starting another is not possible until it ends.")

        @st.fragment(run_every=POLL_SECONDS if active else None)
        def live_panel():
            monitor(client, run_id)
        live_panel()

    st.divider()
    st.subheader("Start a live analysis")
    options = {s.id: s for s in PUBLIC_SCENARIOS}
    choice = st.radio("Scenario", list(options), format_func=lambda k: options[k].title,
                      captions=[options[k].description for k in options], disabled=active)
    sure = st.checkbox("I understand this starts a real run that uses a limited budget.", disabled=active)
    token = st.session_state.setdefault("request_token", web_client.new_request_token())
    if st.button("Start Live Analysis", type="primary", disabled=active or not sure):
        result = client.start(web_client.current_id_token(), choice, token)
        if not result.get("ok"):
            _fail(result)
        elif result["status"] in ("STARTED", "REATTACHED") and result.get("run_id"):
            st.session_state["run_id"] = result["run_id"]
            st.session_state["request_token"] = web_client.new_request_token()
            st.rerun()
        else:
            st.warning(result.get("message") or "The run was not started.")
    if st.button("Sign out"):
        st.logout()


main()
