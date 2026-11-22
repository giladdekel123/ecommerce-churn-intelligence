# V3 spending controls: what is guaranteed and what is not

Status: review of the offline Phase 1 ledger. **The $0.50 reservation and the $5 monthly target are limits on what the system will let start. They are not guarantees about what Google will charge.**

## 1. What the ledger does guarantee (and how it is tested)

| Guarantee | Test evidence |
|---|---|
| **Reservations are atomic.** The single run slot, the daily limit and the monthly budget are checked and the start recorded inside one `BEGIN IMMEDIATE` transaction | 16 threads with different tokens give exactly one run; 16 threads with the same token give exactly one run; two `Ledger` objects on one file give one run; **six separate OS processes** looping on one database reserve exactly 8 x $0.25 = $2.00 and never double-book the slot |
| **Idempotent starts.** A repeated request token never creates a second run, even after the first run finished | service tests: repeat, refresh after finish, simultaneous double click |
| **Interrupted runs cannot make a run free.** A reservation that nobody completed is kept (state INTERRUPTED), and it counts toward both the daily limit and the budget | crash loops stop at the budget (`RESERVED, RESERVED, BUDGET`) and at the daily limit |
| **A dead or killed run keeps its charge and its daily count** | dead-process, timeout and kill-switch tests |
| **A running job the ledger lost track of is adopted and supervised.** If the service died after the launcher started a run but before the ledger recorded it, the supervisor links the live run named by the launcher's lock to the waiting reservation, so the timeout and kill switch apply | five adoption tests, including the cases where nothing may be adopted |
| **Restarts do not reset anything.** A new process sees the same rows and keeps enforcing the timeout | restart test |
| Money is released only when the launch is **known** to have failed (the launcher raised) | launcher-failure tests |

A gap found and fixed in this review: previously a reservation older than 120 s was released as `LAUNCH_FAILED` with the money returned. If the service had died *after* the pipeline started, that run would have been free and unsupervised. The outcome of such a reservation is unknown, so it is now kept and counted.

## 2. Why $0.50 and $5 are not absolute guarantees

1. **The reserve is an estimate, not a proven ceiling.** The $0.48 figure in the Phase 0 record is a ceiling only *if* an output-token cap of 8,192 is set, the cap also limits thinking tokens (unverified), a run makes at most 18 model calls, and inputs stay under 20,000 tokens per call. **The cap is deliberately left unset until supervised measurements exist, so today no such ceiling holds.** Without a cap, the largest single response the model permits (65,536 tokens, about $0.16) times at most 18 calls (6 agents x 3 iterations) is a theoretical $2.95 per run, before input charges. That would exceed the $0.50 reserve six times over. The real cost is probably far lower (estimate $0.02 to $0.13, uncertain), but nothing in the code enforces it.
2. **Token usage is not measured yet.** Thinking tokens are billed at the output rate and have never been recorded.
3. **Measured overspend cannot raise the charge yet.** The ledger charges `max(reserve, measured cost)`, but nothing feeds the measured cost into it in Phase 1. A run that cost more than its reserve is invisible to the ledger.
4. **Stopping a run does not un-bill it.** The timeout and kill switch end the process, but requests already sent are billed, and whether tokens generated after a client disconnect are billed is not verified.
5. **Prices and rules can change.** The reserve is in dollars, the prices are Google's. A price rise, or long-context pricing, is not modelled.
6. **Library retries are unverified.** CrewAI's setting is `max_retries=0`, but whether the underlying Google client library retries internally (and bills each attempt) was not checked.
7. **The ledger only sees what passes through it.** A leaked or shared key used elsewhere spends money the ledger never records. Only Google-side controls limit that.
8. **The ledger is local state.** Anyone with file access can edit it. **Restoring the host from a backup restores old counters**, which would reset recent usage.
9. **It follows the server's UTC clock.** A forward clock jump opens a new day (the monthly budget still holds); a backward jump does not reopen a used day (both tested). Wrong time near a month boundary can reset the monthly budget. The clock must stay NTP-synchronized.
10. **One ledger, one host.** A second host or a second ledger file is a second budget. A run is charged to the month in which it was requested.
11. **Refused or denied attempts are free and unlimited.** Busy, over-limit and unauthorized attempts cost nothing but are not rate-limited; a reverse-proxy limit is advisable.

## 3. Google-side backstops are delayed and partial
| Control | Limit of the control |
|---|---|
| Prepaid balance on a dedicated billing account | The nearest thing to a hard ceiling, but it draws down "within minutes", and at $0 it stops **every key on that billing account**. Minimum $5, credits expire after 12 months, no refunds, availability for the owner's account unknown |
| Project monthly spend cap (AI Studio) | Labelled experimental; overage possible during about a 10-minute lag; batch/agent work may exceed it |
| Usage and billing views | Up to 24 hours (Cloud Billing sometimes longer) |
| Budget alerts | Alert only; do not cap |
| Key restricted to the Gemini API and the server IP | Limits who can use the key, not how much |

So the honest statement is: **the ledger bounds the number of runs that can start and the amount it reserves; the prepaid balance is the only hard dollar ceiling, and it is not instantaneous.**

## 4. The output-token cap stays unset
`CREW_GEMINI_MAX_OUTPUT_TOKENS` is not set anywhere. When supervised runs exist, decide with data:
1. Run one PASS and one FAIL scenario with `CREW_RECORD_TOKEN_USAGE=1` and compare the counters with Google's dashboard (a day later).
2. Record, per call if possible, completion and reasoning tokens, and whether every report is complete (not cut off).
3. Set the cap to roughly 1.5 times the largest observed single-call completion (never below the 2,048 floor), then re-derive the reserve from the cap: reserve = calls x cap x output price + calls x input bound x input price, rounded up.
4. Re-check after any model or price change.

## 5. Next-phase actions
- Feed the measured usage into `ledger.finish(actual_micro=...)` and alert when a run exceeds its reserve.
- Reconcile the ledger's monthly total with Google's dashboard once a month and record the drift.
- Keep the ledger and the allow-list out of restore-from-backup surprises (document the procedure; consider an append-only log).
- Add proxy-level rate limiting and a deny log for repeated refused attempts.
- Verify whether the Google client library retries internally.
