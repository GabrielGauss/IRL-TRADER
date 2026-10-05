# The journey: from an empty skeleton to an IRL-gated bot with a canary

This is the story of how the project got to where it is: what was built,
which decisions shaped it, what broke along the way, and why the last piece
built was a canary that never trades. It's written for anyone picking up the
codebase, including future us.

## 1. A bot that works before it is clever (late August 2026)

The first goal was a trading bot that is **correct and safe** before it is
**profitable**:

- typed config with a hard testnet default; going live needs **two separate opt-ins** (`USE_TESTNET=false` *and* `I_UNDERSTAND_LIVE_TRADING_RISK=true`),
- an EMA/RSI strategy, risk-based position sizing, a backtester, SQLite persistence,
- an execution engine that reads positions from the exchange (survives restarts) and **never blindly retries** order placement (a retry can double-execute a real trade),
- a CLI (`backtest`, `run`), all built test-first.

Then a second, **signal-agnostic** stack was built in four stages: external
systems POST JSON signals; an ingestion queue validates them; a portfolio,
drawdown monitor and latching kill switch size and gate them; an async,
ccxt-based broker layer (live or paper) executes; a controller ties it
together with health, metrics and a JSON audit trail (`serve`).

The design choice that mattered later: **the bot doesn't need to know where
signals come from.** That made it a good test client for something else.

## 2. Making it deployable (4 Oct 2026)

- **CI:** ruff, black, isort, mypy and pytest with a 90% coverage floor, on Ubuntu and Windows.
- **Webhook auth:** `POST /signals` takes a shared secret (header, or a `passphrase` body field for TradingView, which can't set headers). The server **fails closed**: it won't start without a secret.
- **TradingView compatibility:** its `{{strategy.order.action}}` placeholder renders lowercase `buy`. That was silently dropped *after* the webhook had already returned 202, so the sender believed it worked. Actions are now case-insensitive.
- **Docker deployment behind an existing nginx:** only `POST /signals` is public; `/health` and `/metrics` stay internal.
- **A safety bug found while writing the restart policy:** the kill switch only latched *in memory*. Docker's `restart: unless-stopped` would have restarted the bot after a drawdown trip with a fresh kill switch, **resuming trading after hitting the loss limit**. A trip now writes `kill_switch.tripped` and the bot refuses to start until a human deletes it.

## 3. Why the bot exists: testing IRL for real (4–5 Oct 2026)

The bot was always meant to exercise the **IRL Engine**, a pre-execution
compliance gateway that seals an autonomous agent's decision before it reaches
an exchange (authorize → place → bind) into a tamper-evident,
Bitcoin-anchored audit chain.

So every order now goes through an **order gate**:

1. the router *plans* the order (`OrderPlan`) without placing it,
2. `IrlGate` authorizes the intent with IRL, sending a fresh `irl-<uuid>` client order id,
3. the order is placed **with that same id**, linking the exchange order to the IRL trace,
4. the fill is **bound** back to the trace.

It fails closed throughout: a denial, an unreachable IRL or any IRL error
means **no order** (audited as `order blocked`). A failed bind after a real
fill never crashes, because the trade already happened, so it's logged for
reconciliation instead.

The client was written against IRL's OpenAPI spec rather than its Python SDK,
which blocks the event loop and, it turned out, had drifted from the server.

## 4. What a real client uncovered

The first real trade through production IRL didn't succeed for a while, and
each failure peeled back one layer. In every case the bot did the right
thing: no order, an audit entry, keep running.

| The bot saw | What was actually wrong in IRL |
|---|---|
| `KeyError: 'id'` on agent registration (the agent was created anyway) | Server returns `agent_id`; IRL's own SDKs read `id`. |
| `HEARTBEAT_MISSING` | Production requires Layer-2 signed heartbeats; the bot now fetches a fresh one before every authorize. |
| `HEARTBEAT_SIGNATURE_INVALID` | IRL's configured public key didn't match the regime provider's signing key, so **every** client was rejected, for months. |
| `DATABASE_ERROR` (with nothing in IRL's logs) | A migration recorded as applied had never reached the live table: every trace insert failed. Found in Postgres's own log. |
| `MATCHED` | First successful end-to-end trace. Its final proof was recomputed independently and matched. |

The follow-up review explained why nobody had noticed:

- IRL's health endpoint was a hardcoded `"ok"`,
- database errors were returned as generic 500s without being logged,
- partition maintenance results were discarded (no partitions after July),
- the key-mismatch warning fired every 80 ms and filled the disk with logs,
- **CI had been red since July** while deploys shipped on every push regardless.

All of this was fixed in IRL (real health checks, logged errors, a startup
schema guard, logged partition maintenance, deploys gated on CI, SDKs aligned
with the server plus contract tests) and documented in IRL's own incident
review.

## 5. The lesson that produced the canary

Every one of those faults was found in one morning by a client doing the real
thing end to end, after months of green status. A health check proves a
process is up; only an end-to-end call proves the **path** works.

So the last piece was **`trading-bot irl-canary`**:

- it authorizes a 1 USDT intent with the bot's real identity and heartbeat path,
- then binds it as **Rejected**, which IRL seals as `MATCHED` without any order being placed,
- so it exercises auth, model hash, heartbeat, signature verification, policy and the trace/bind database writes: **exactly the paths that broke**.

A small host-maintenance tool runs it every 30 minutes, alongside checks for
disk, containers, real health status, TLS expiry, database partitions,
backups and the kill switch, and emails once per failure (with reminders and
a recovery notice). If IRL breaks again, it'll be known within half an hour,
not months.

Two more operational fixes came out of the same pass: the paper account is now
**persisted across restarts** (a restart had silently reset it), and the host
got log rotation, verified nightly backups, and a certificate-renewal
container that actually restarts after a reboot.

## 6. Principles that emerged

1. **Fail closed, and say why.** Every refusal (bad config, missing secret, IRL denial, latched kill switch) stops cleanly with one clear message and leaves an audit record.
2. **Never discard an error.** Swallowed errors, `let _ =`, and log spam hid a production outage for months.
3. **Verify the real thing.** Check the live schema, call the real endpoint, recompute the proof yourself.
4. **Deploys follow tests, not pushes.**
5. **Correct before clever.** The bot still has no proven trading edge. That's the next phase, deliberately last.

## What's next

1. An **honest backtester**: fees, slippage, out-of-sample / walk-forward testing, and a buy-and-hold benchmark. Without it, no strategy result means anything.
2. An internal strategy as one signal source, a **signal combiner**, and **per-source webhook credentials** before a second sender is added.
3. **IRL Layer 2 v2:** move regime binding server-side, so remote clients no longer need a second credential or a 200 ms cross-service window.
4. Weeks of **paper trading** on the server → **Binance testnet** → **small live** (< $500, spot only, tight kill switch).
