"""Architecture V3 live-execution core (offline Phase 1).

Nothing in this package calls Gemini, opens a network port or starts a service by itself.
It is the security-focused core that a later Streamlit front end will call:
auth -> scenarios -> ledger -> service -> supervisor, with sanitized projections out.
The existing pipeline (src/), run_launcher and the independent gate are reused unchanged.
"""
