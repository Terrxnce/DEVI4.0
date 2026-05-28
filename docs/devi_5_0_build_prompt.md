# D.E.V.I 5.0 — Build Specification Prompt
**Divine Earnings Virtual Intelligence — Production SaaS Trading System**
**Version: 5.0 | Prepared for: AI-assisted full-system build**

---

## CONTEXT AND AUTHORITY

You are building D.E.V.I 5.0 — the commercial trading intelligence product for
Divine Earnings (divineearnings.com). The brand motto is "Where Wisdom Meets
Wealth." The brand colours are purple (#6F63E7) and black (#000000).

This is not a prototype. It is a production SaaS product that will be sold to
subscribers and used for real capital. Every architectural decision must reflect that.

The previous version, D.E.V.I 4.0, was a local Python system that proved the
core execution pipeline. The validated findings from that system are embedded in
this specification. Do not ignore them — they represent real forward-tested
behaviour on a live MetaTrader 5 demo account.

---

## PRODUCT SUMMARY

D.E.V.I 5.0 is an autonomous algorithmic trading system delivered as a SaaS web
application. Users connect their existing MetaTrader 5 account to the platform
via a lightweight local bridge agent. D.E.V.I analyses markets using ICT and SMC
methodology across multiple timeframes, identifies high-probability trade setups,
executes with strict risk controls, and explains every decision in plain language
via a built-in LLM assistant.

Users access everything through the D.E.V.I Dashboard — an extension of the
existing Divine Earnings Desk (divine-earnings-desk.vercel.app). The dashboard
provides live charts, trade history, bot controls, risk settings, and a direct
chat interface to D.E.V.I's reasoning engine.

The product must support:
- FTMO evaluations and funded account rules
- Personal retail accounts from EUR 50 upward
- High-frequency mode on personal broker accounts (Vantage, StarTrader)
- Multi-user SaaS architecture (each user has isolated account context)
- Three risk tiers: Low, Mid, High

---

## SYSTEM ARCHITECTURE OVERVIEW

The system has four layers:

```
[User's Windows PC]
  MT5 Terminal
      |
  D.E.V.I Bridge Agent (local Python service)
      |  (secure WebSocket — persistent connection)
      |
[D.E.V.I Cloud Server]
  Analysis Engine
  Execution Scheduler
  LLM Reasoning Layer
  WebSocket Gateway
  REST API
      |
[D.E.V.I Dashboard — Web App]
  Next.js frontend
  TradingView charts
  Bot controls
  LLM chat interface
  Account management
```

### Layer 1: D.E.V.I Bridge Agent (runs on user's PC)

A lightweight Python service the user installs once on their Windows machine.
It does not require any MetaTrader knowledge to set up — the installer handles it.

Responsibilities:
- Connect to local MT5 terminal via MetaTrader5 Python library
- Maintain a persistent authenticated WebSocket connection to D.E.V.I Cloud
- Relay market data (bars, ticks, account info, positions) to the cloud on request
- Receive execution commands from the cloud and forward to MT5 via order_send
- Return execution results (retcode, ticket, fill price) back to the cloud
- Run as a Windows background service (auto-starts with Windows)
- Display a system tray icon showing connection status (green/red)

The Bridge Agent never makes trading decisions. It is a dumb relay.
All logic lives in the cloud. The bridge only executes what it is told.

Security model:
- Each user has a unique bridge token issued by the cloud server
- All communication is encrypted (TLS WebSocket)
- Bridge token can be revoked remotely (disconnects the bridge instantly)
- Bridge agent checks a hardware fingerprint on startup to prevent token sharing
- Bridge only accepts commands from the authenticated D.E.V.I Cloud server

### Layer 2: D.E.V.I Cloud Server

Built with: Python 3.11+, FastAPI, Celery (task scheduling), Redis (message
broker and cache), PostgreSQL (persistent data), WebSocket server.

Responsibilities:
- Run the M15 analysis cycle for every connected user account
- Schedule the cycle correctly per session window and M15 bar boundary
- Store all decisions, trades, positions, and account state per user
- Serve the REST API and WebSocket feed to the dashboard
- Manage LLM reasoning requests
- Handle billing and subscription state (Stripe integration)
- Enforce per-user isolation — no data or execution state bleeds between users

Multi-tenancy model:
- Each user has a tenant ID
- All analysis and execution runs in isolated task contexts
- User A's kill switch cannot affect User B's bot
- Logs, positions, and decisions are per-user

### Layer 3: D.E.V.I Dashboard (Web App)

Extends the existing divine-earnings-desk.vercel.app frontend.
Built with: Next.js 14+, TailwindCSS, TradingView Lightweight Charts,
shadcn/ui components, Clerk or Auth.js for authentication.

Brand: purple #6F63E7 / black #000000.

Core pages:
- /dashboard — live overview (session status, open positions, P&L, bot status)
- /charts — TradingView-style chart with D.E.V.I structure overlays
- /trades — full trade history with entry/exit context and D.E.V.I reasoning
- /settings — risk mode, session preferences, pair selection, account connection
- /devi — LLM chat interface (ask D.E.V.I anything about its decisions)
- /account — subscription, billing, bridge connection status

---

## TRADING STRATEGY: ICT / SMC METHODOLOGY

D.E.V.I 5.0 uses a top-down multi-timeframe analysis based on ICT (Inner Circle
Trader) and SMC (Smart Money Concepts) principles. The system identifies where
institutional money has left footprints in market structure and trades in the
direction of that flow.

### Timeframe Hierarchy

| Timeframe | Role |
|-----------|------|
| 4H | Macro bias, major liquidity pools, premium/discount zones |
| 1H | Intermediate structure, session-level swing highs/lows |
| 15M | Execution timeframe — primary entry and confirmation |
| 5M | Entry refinement, precision entries within 15M structure |
| 1M | HFT mode only (personal accounts, Vantage/StarTrader) |

Analysis runs top-down on every cycle. The 4H and 1H context is computed once
per hour. The 15M and 5M context is computed at every M15 bar boundary.

### Structure Concepts (all must be detected and mapped)

**Market Structure:**
- Break of Structure (BOS): confirmed break of the most recent swing high or low
  in the direction of the prevailing trend
- Change of Character (CHOCH): first break against the prevailing trend;
  signals potential reversal — treated as higher-risk entry signal
- Market Structure Shift (MSS): strong impulsive break with momentum confirmation

**Liquidity:**
- Equal Highs (EQH): two or more swing highs at approximately the same price
  level (within 0.5× ATR) — treated as a liquidity pool above price
- Equal Lows (EQL): two or more swing lows at approximately the same price
  level — treated as a liquidity pool below price
- Swing High (SH): most recent peak in current timeframe structure
- Swing Low (SL): most recent trough in current timeframe structure
- Inducement: a minor liquidity grab before the real move (used to anticipate
  the actual entry point)

**Price Delivery:**
- Order Block (OB): the last up-candle before a bearish move (bearish OB) or
  last down-candle before a bullish move (bullish OB). Must be unmitigated
  (price has not returned and closed through it). Maximum age: 20 bars on
  execution timeframe.
- Breaker Block: an OB that has been mitigated and flipped — now acts as
  opposing structure. Lower-probability than a fresh OB.
- Inverse Fair Value Gap (IFVG): a gap that has been partially mitigated;
  the remaining portion acts as a magnet for price to return to
- Fair Value Gap (FVG): three-candle imbalance where the first candle's high
  and third candle's low do not overlap (bullish) or first low and third high
  do not overlap (bearish). Price is expected to return to fill it.
- Mitigation Block: a candle cluster where an OB has been partially traded
  through; residual OB energy remains

**Premium / Discount:**
- On every timeframe, the range between the current session's swing high and
  swing low is divided into a 0-100 scale
- Above 50 (midpoint) = premium (look for shorts)
- Below 50 = discount (look for longs)
- Optimal Trade Entry (OTE): between 62% and 79% retracement of the swing
  (Fibonacci 0.618-0.786) — this is the highest-probability entry zone

### Entry Logic

A trade is entered when all of the following are satisfied:

1. **Macro bias confirmed (4H):**
   The 4H structure has a clear BOS in one direction within the last 5 candles.
   Price is in the discount zone (for longs) or premium zone (for shorts) on 4H.

2. **Intermediate structure aligned (1H):**
   1H BOS agrees with 4H bias, OR 1H is consolidating (neutral — allowed with
   penalty) but NOT showing a CHOCH against the 4H direction.

3. **Execution timeframe setup (15M):**
   At least TWO of the following structures must be present and aligned:
   - Unmitigated OB in the direction of trade within 20 bars
   - FVG in the direction of trade (price is trading into it)
   - BOS confirming the direction on 15M
   - Liquidity sweep (price swept EQH/EQL or SH/SL) followed by a displacement
     candle in the opposite direction
   - IFVG acting as support/resistance at the entry zone
   - Price at OTE retracement level (62-79% of the most recent swing)

4. **Entry refinement (5M):**
   A 5M confirmation candle: either an engulfing candle, a BOS on 5M, or
   a strong rejection wick at the entry zone.
   This candle must close before the order is placed (no anticipation entries).

5. **RR minimum:**
   Take Profit must be at the next opposing liquidity pool (nearest EQH for
   shorts, nearest EQL for longs, or nearest swing high/low).
   Minimum R:R: 1.5 in Low and Mid risk modes. 1.2 in High risk mode.
   If TP target does not produce minimum R:R, the trade is rejected.

6. **No trade if any hard block is active** (see Risk Management section).

### Exit Logic

**Stop Loss placement:**
- Below the OB low + 0.3× ATR buffer for long trades
- Above the OB high + 0.3× ATR buffer for short trades
- Absolute minimum SL: 0.5× ATR(14) on 15M
- Absolute maximum SL: 2.5× ATR(14) on 15M
- If the calculated SL falls outside the ATR bounds, the trade is rejected

**Take Profit placement:**
- Primary TP: opposing liquidity level (EQH or EQL on 15M or 1H)
- If no clear liquidity pool is visible, TP = 2× SL distance from entry
- Partial exit 1: close 50% of position at 1R profit
- On partial exit: move SL to breakeven (entry price)
- Partial exit 2 (optional, user-configurable): close 25% at 1.5R
- Trail remaining position: ATR-based trail at 0.5× ATR(14) from current price
  once price has moved 1.5R in favour

**Hard time exit:**
- Any position open longer than 6 hours without reaching 1R profit is closed
- Exception: do not close before a major session open if the trade is still valid
- Friday close: all positions closed by 20:00 UTC on Friday

**HFT mode adjustments (1M, personal accounts only):**
- SL: 0.3× ATR(14) on 1M
- TP: 1× ATR(14) on 1M
- Maximum position duration: 30 minutes
- No partial exits — binary: full TP or full SL
- Only during first 30 minutes of London and NY AM opens

---

## SESSION MANAGEMENT

D.E.V.I is session-aware. Pairs and instruments are only traded in their
natural sessions. The economic calendar blocks trading 30 minutes before and
after any high-impact news event for affected pairs.

### Session Windows (UTC)

| Session | Open | Close | Primary Instruments |
|---------|------|-------|---------------------|
| Asia | 23:00 | 02:00 | USDJPY, AUDUSD, NZDUSD, XAUUSD (thin) |
| London | 07:00 | 12:00 | EURUSD, GBPUSD, EURGBP, GBPJPY, XAUUSD |
| New York AM | 13:00 | 17:00 | EURUSD, GBPUSD, USDJPY, USDCAD, XAUUSD, US30, NAS100, SPX500 |
| New York PM | 17:00 | 20:00 | USDCAD, USDCHF, USDJPY (lower liquidity) |

Rules:
- No new entries outside the above windows
- Positions opened in a session may remain open into the next session
- No new entries within 30 minutes of session open (avoid the first sweep candle)
  unless an explicit session-open liquidity grab setup is configured
- No trading Sunday before 21:00 UTC or Friday after 20:00 UTC

### Economic Calendar Integration

Source: ForexFactory API or Investing.com calendar (fetch daily at midnight UTC)

Rules:
- High-impact events: block the affected currency pair from 30 minutes before
  to 30 minutes after the scheduled release time
- Medium-impact events: reduce position size by 50% for the affected pair
  during the 15-minute window
- Low-impact events: no restriction
- "Affected pair" means any pair containing that currency
  (e.g. USD NFP blocks EURUSD, GBPUSD, USDJPY, USDCAD, USDCHF, AUDUSD, NZDUSD)
- If a position is already open when a high-impact event is approaching,
  the bot warns the user via dashboard but does not auto-close unless
  the user has enabled "auto-close before news" in settings

---

## RISK MANAGEMENT

Risk is calculated per trade, per session, and per account state.
All values are configurable. Hard limits cannot be overridden from the dashboard.

### Risk Modes

| Parameter | Low Risk | Mid Risk | High Risk |
|-----------|----------|----------|-----------|
| Risk per trade | 0.5% | 1.0% | 2.0% |
| Max open positions | 2 | 3 | 5 |
| Max daily drawdown (soft block) | 1.5% | 3.0% | 4.0% |
| Max daily drawdown (hard block) | 2.5% | 4.5% | 6.0% |
| Minimum R:R | 1.5 | 1.5 | 1.2 |
| Confluence minimum | Tier A only | Tier A + B | Tier A + B + C |
| Max positions per pair | 1 | 1 | 2 |

### FTMO Compliance Mode

Activated automatically when user selects "FTMO Evaluation" in account type.

Hard constraints (cannot be disabled):
- Daily drawdown cap: 4.5% of starting balance (buffer below FTMO 5%)
- Total drawdown cap: 9.0% of starting balance (buffer below FTMO 10%)
- When daily PnL reaches -3.0%: risk per trade halved for rest of session
- When daily PnL reaches -4.0%: trading stopped for that calendar day
- No martingale. No position averaging. No revenge logic.
- No HFT mode (1M timeframe disabled)
- No trading in the 2 hours before the FTMO evaluation deadline
- Consistency rule: flag any single trade that exceeds 35% of total profit
  earned so far — notify user to review

Tracking:
- D.E.V.I tracks calendar trading days with at least one closed trade
- Minimum trading days progress shown on dashboard
- Projected evaluation completion date shown based on current pace

### Small Account Mode (EUR 50 - EUR 500)

Activated automatically when account balance is detected below EUR 500.

Adjustments:
- Risk per trade: 1.0% regardless of risk mode setting
- Only EURUSD and GBPUSD (tightest spreads)
- Maximum 1 open position at a time
- Only London session and NY AM session
- Spread cap: 0.8 pips maximum — reject trade if spread exceeds this
- No XAUUSD, no indices
- Minimum lot size validated against broker before order is placed
  (if dynamic lot < broker minimum, reject the trade — never round up)

### Lot Sizing

Lot size is always calculated dynamically from:
- Account balance in account currency
- Risk percentage (from active risk mode)
- SL distance in price terms
- Symbol pip value and contract size (fetched from MT5 per symbol)
- Broker minimum and maximum lot constraints

Formula (validated in D.E.V.I 4.0):
```
risk_amount = balance * (risk_pct / 100)
sl_points = abs(entry - sl) / point_size
point_value = contract_size * point_size
loss_per_lot = sl_points * point_value
lot_size = floor((risk_amount / loss_per_lot) / lot_step) * lot_step
lot_size = clamp(lot_size, min_lot, max_lot)
```

Fixed lot mode: available for experienced users who want manual lot control.
When active, the fixed lot is used directly. Drawdown and position guards
still apply.

### Spread Cap

The spread cap is defined in pips per symbol. Internally converted to price:
```
spread_max_price = spread_max_pips * 10 * point_size
```
This is the CORRECT formula. Using `spread_max_pips * point_size` alone is wrong
and produces a cap 10x tighter than labelled (a known D.E.V.I 4.0 bug, fixed).

Default spread caps by asset class:
- Major FX: 1.5 pips
- Minor FX: 2.0 pips
- JPY pairs: 1.5 pips
- XAUUSD: 3.0 pips
- Indices (US30, NAS100, SPX500): 2.0 points

---

## SAFETY ARCHITECTURE

All of the following are mandatory. None can be disabled.

### Execution Gates (in order)

1. **Arming token**: valid token with approved symbols and expiry required
2. **Kill switch**: global or per-symbol halt must be clear
3. **Max orders per run**: hard cap on orders per cycle
4. **Pre-trade recheck**: spread, lot size, account state re-validated immediately
   before order_send (not just at decision time)
5. **Bridge validation**: bridge agent must be connected and healthy
6. **Session gate**: current time must be within an active session window
7. **News gate**: no high-impact news within 30 minutes for this pair

### Position State

- All position state is synced from MT5 at the START of every cycle
- D.E.V.I never trusts its own internal memory as ground truth
- MT5 is always the source of truth for open positions
- A position that disappears from MT5 (closed externally) triggers a
  position_close event and logs an explanation ("closed_externally")

### Kill Switch

- Can be triggered: manually from dashboard, automatically on breach
- Automatic triggers: consecutive failures > 3, daily drawdown hard cap hit,
  unexpected equity spike > 5% in one cycle
- When triggered: blocks all new orders immediately
- Optional: auto-close all open positions (user-configurable)
- Reset requires: operator confirmation via dashboard + reason code

### Emergency Close

A dedicated command available on the dashboard at all times.
Closes one position (by ticket) or all positions simultaneously.
Works even when kill switch is active.
Logs every emergency close with timestamp, trigger source, and account state.

### Telemetry and Auditability

Every execution decision, trade attempt, and position event is written to
persistent storage. The following records are mandatory for every trade:
- decision_id, trade_id, mt5_ticket (full linkage chain)
- Entry price (intended and actual), SL, TP, lot size
- Broker retcode, slippage, spread at fill
- Setup type, confluence score, active structures
- Session, economic calendar state, risk mode at time of trade
- All gate verdicts (arming, kill switch, spread, risk evaluator, recheck)

If any field is missing, the trade record is flagged as incomplete.

---

## SLIPPAGE AND RETCODE HANDLING

From D.E.V.I 4.0 live testing:

**MT5 returns price=0 on some fills (broker-dependent).**
This causes slippage = 0 - entry_price = a large garbage negative number.
Guard: if actual_fill_price <= 0, fall back to intended_entry_price.
Slippage = 0.0 in this case. Log a warning so it can be audited.

**MT5 retcode handling (all retcodes must be mapped):**

| Retcode | Meaning | Action |
|---------|---------|--------|
| 10009 | Request completed (fill) | Record as FILLED |
| 10004 | Requote | Retry once with new price from tick |
| 10006 | Request rejected | Log, no retry |
| 10015 | Invalid price | Retry once with updated price |
| 10016 | Invalid stops | Log invalid SL/TP, reject trade |
| 10019 | No money | Log, trigger kill switch |
| 10010 | Only part of position filled | Accept partial, log |
| 10014 | Invalid volume | Log, reject |
| Other | Unknown error | Log retcode, trigger kill switch failure counter |

Record failure (for kill switch counter): retcodes 10006, 10016, 10019, unknown.

---

## LLM REASONING LAYER

D.E.V.I 5.0 includes a natural language interface powered by an LLM
(Claude Sonnet or GPT-4o — configurable per deployment).

### What D.E.V.I Can Explain

The LLM has access to the full decision context for every trade:
- All active structures at time of decision (OB locations, FVG zones, BOS)
- Multi-timeframe analysis snapshot (4H bias, 1H structure, 15M setup)
- Confluence score and tier
- Gate verdicts (what passed, what failed and why)
- Session and news state
- Entry/exit prices, SL/TP, actual fill
- Post-trade: whether SL or TP was hit, final PnL, duration

### Example Queries D.E.V.I Can Answer

- "Why did you take this EURUSD trade at 09:15?"
  → D.E.V.I explains the 4H discount zone, the 1H BOS, the 15M OB+FVG setup,
    the confluence score, and why the R:R cleared the minimum.

- "Why did this GBPUSD trade lose?"
  → D.E.V.I explains the setup was valid at entry, that the 1H structure
    held but the 4H trend reversed unexpectedly, and that SL was at the
    structural invalidation level — loss was within plan.

- "Why was this USDJPY trade rejected?"
  → D.E.V.I shows which gate failed (e.g. H1 contradiction, spread too wide,
    RR below minimum) and explains what would have needed to be different.

- "What does D.E.V.I see on EURUSD right now?"
  → D.E.V.I describes the current multi-timeframe structure, identifies
    pending liquidity pools, and states whether it is looking for longs,
    shorts, or waiting.

- "How is the bot performing this week?"
  → D.E.V.I gives a plain-language performance summary: trades taken,
    win rate, biggest winner, biggest loser, current drawdown.

### LLM Architecture

The LLM is not given raw price data. It is given structured context objects
that D.E.V.I's analysis engine produces. This keeps the context window small
and the responses factual rather than hallucinated.

Context object passed to LLM per query:
```json
{
  "query_type": "explain_trade | explain_rejection | explain_loss | current_view | performance",
  "trade_id": "...",
  "symbol": "EURUSD",
  "session": "LONDON",
  "timestamp": "...",
  "analysis": {
    "h4_bias": "BEARISH",
    "h4_premium_discount": "PREMIUM",
    "h1_structure": "BOS_BEARISH",
    "m15_setup": ["OB_BEARISH", "FVG_BEARISH", "LIQUIDITY_SWEEP"],
    "confluence_score": 0.82,
    "confluence_tier": "A",
    "active_structures": [...],
    "entry_price": 1.17093,
    "sl": 1.17220,
    "tp": 1.16928,
    "rr": 1.69
  },
  "execution": {
    "order_status": "FILLED",
    "lot_size": 0.01,
    "actual_fill": 1.17093,
    "slippage": 0.0,
    "broker_retcode": 10009
  },
  "outcome": {
    "close_reason": "SL_HIT | TP_HIT | MANUAL | TIME_EXIT",
    "close_price": ...,
    "pnl_r": ...,
    "duration_minutes": ...
  },
  "gates": {
    "arming": "PASS",
    "kill_switch": "PASS",
    "spread": "PASS",
    "news": "PASS",
    "risk_evaluator": "PASS",
    "reason": "approved"
  }
}
```

The LLM is prompted with D.E.V.I's personality: serious, intelligent, transparent,
system-driven. It does not hype trades. It does not guarantee results. It explains
methodology and facts. It references the ICT/SMC structures by name.

---

## WEB DASHBOARD REQUIREMENTS

### /dashboard (Home)

- Session clock showing current active session and time remaining
- Bot status: ARMED / SCANNING / HOLDING / PAUSED / KILL_SWITCH
- Live open positions with floating PnL, entry, SL, TP
- Today's stats: trades taken, win rate, PnL%, drawdown used
- Active pairs: which symbols D.E.V.I is currently watching
- Recent decision log: last 10 scan cycle outcomes per symbol
- Quick controls: Pause, Resume, Emergency Close All
- Risk mode selector: Low / Mid / High (requires confirm dialog to change)

### /charts

TradingView Lightweight Charts (via TradingView charting library or
Lightweight Charts library — free version acceptable).

Overlay visualisation (drawn by D.E.V.I, not manual):
- Order blocks: coloured boxes (bull OB = green, bear OB = red)
- FVG zones: shaded rectangles
- BOS / CHOCH lines: horizontal lines at break levels
- Equal Highs / Equal Lows: dashed horizontal lines
- Current SL and TP for open positions: horizontal lines with labels
- Premium / Discount zones: shaded background on chart
- Entry markers: arrows on the execution candle

Timeframe switcher: 4H, 1H, 15M, 5M (1M visible only in HFT mode)

### /trades

Full trade history table with:
- Symbol, direction, entry time, close time
- Entry, SL, TP, actual close price
- R multiple achieved (e.g. +1.5R, -1.0R)
- PnL in account currency and percentage
- Setup type (OB+FVG, OB+BOS, etc.) and confluence tier
- Session active at entry
- "Ask D.E.V.I" button on each row — opens LLM chat pre-loaded with that trade

Filters: date range, symbol, session, outcome (win/loss/breakeven), setup type

### /devi (LLM Chat)

Full-page chat interface.
D.E.V.I avatar (use brand logo / purple AI aesthetic).
Pre-loaded suggested questions:
- "What is D.E.V.I watching right now?"
- "Explain my last losing trade"
- "How is the bot performing this week?"
- "Why was this pair rejected?"

Chat history is stored per user. D.E.V.I remembers conversation context within
a session (standard LLM context window).

### /settings

Account connection:
- Bridge status (connected / disconnected / last seen timestamp)
- MT5 account number (read from bridge)
- Account type: Retail / FTMO Evaluation / HFT Personal
- Download link for D.E.V.I Bridge Agent installer

Risk configuration:
- Risk mode: Low / Mid / High
- Override per-trade risk % (within mode limits)
- Session preferences: enable/disable individual sessions
- Pair preferences: enable/disable individual pairs
- Spread cap overrides per symbol
- Auto-close before news: on/off
- Emergency close behaviour: close all / close per symbol

Notifications:
- Telegram bot token (for trade alerts)
- Discord webhook (for trade alerts)
- Email (for daily summary and drawdown warnings)

---

## VALIDATED BASELINE FROM D.E.V.I 4.0

The following is confirmed real behaviour from D.E.V.I 4.0 running on a live
MetaTrader 5 demo account. This is the foundation D.E.V.I 5.0 builds on.
Do not re-derive these findings — they are proven.

### What works and is carry-forward:

**Execution pipeline:**
- MT5 Python library integration for bars, ticks, account info, positions — stable
- Order_send with full request dict (action, symbol, volume, type, price, sl, tp,
  deviation, magic, comment, type_filling) — fires correctly
- Pre-trade recheck (spread, lot size, exposure) immediately before send — works
- Arming token + kill switch gates — enforce correctly in all test scenarios
- Supervisor gate (max orders, arming, kill switch) — tested, 218 tests passing
- Paper vs live path separation — verified no paper run can call real order_send

**Risk evaluator:**
- Dynamic lot sizing using the formula above — verified correct on EURUSD 20-pip SL:
  0.4% risk on $10,000 = $40 risk; loss_per_lot = $200; lot = 0.2 — matches
- Fixed lot mode: when dynamic_lot_sizing=false, returns configured lot directly,
  drawdown guards still active — tested and confirmed in 6 unit tests
- Soft reduction: halves risk when daily_pnl_pct <= soft_daily_reduction_pct — works
- All drawdown and position limit gates — tested and confirmed

**Structure detectors (6 confirmed operational):**
- Order Block detector with max_age_bars enforcement
- Break of Structure (BOS) detector
- Fair Value Gap (FVG) detector
- Liquidity Sweep detector
- Rejection candle detector
- Engulfing candle detector

**Confluence engine:**
- Tier A / Tier B / Tier C scoring — operational
- Hard and soft penalties — applied correctly
- OB+BOS and OB+Engulfing are the highest-quality observed setup types
- H1 neutral gate: correctly blocks ~50% of setups during choppy conditions
- H1 contradiction gate: correctly blocks ~25% of setups (opposing trend)
- This is normal and expected — not a bug

**Session filtering:**
- Asia / London / NY AM / NY PM windows — correct
- Outside-session hard block — verified

**Slippage guard (CRITICAL — known bug fixed in 4.0):**
- MT5 returns price=0 on fills from some brokers
- Without the guard: slippage = 0 - 1.17073 = -1.17073 (garbage)
- With the guard: if actual_fill <= 0, fall back to intended_entry; slippage = 0.0
- This guard MUST be present in D.E.V.I 5.0

**Spread cap formula (CRITICAL — known bug fixed in 4.0):**
- WRONG: spread_cap = spread_max_pips * point_size
  (for EURUSD: 5 pips * 0.00001 = 0.00005 — this is 0.5 pips, not 5 pips)
- RIGHT: spread_cap = spread_max_pips * 10 * point_size
  (for EURUSD: 5 * 10 * 0.00001 = 0.0005 — correct 5-pip cap)
- The wrong formula was 10x tighter than labelled and blocked almost everything

**Live test result:**
- First clean live trade: EURUSD, 0.01 lot, OB_WITH_ENGULFING, Tier A
- Retcode 10009, ticket 448463040
- Lot size correct (fixed lot mode working)
- Slippage 0.0 (guard working)
- Spread at fill 0.1 pip (inside cap)
- Cycle 2: existing_open_position correctly blocked re-entry (position state working)
- Session filtering: GBPUSD, USDCHF blocked by h1_neutral_gate; USDJPY by h1_contradiction

### Known gaps in D.E.V.I 4.0 (must be solved in 5.0):

1. No trailing stop or breakeven automation — positions sit with fixed SL/TP
2. No position lifecycle tracking after close — PnL tracking is incomplete
3. No partial close execution — exits are binary (full SL or full TP)
4. No IFVG or Equal Highs/Lows detection — only 6 detectors exist
5. No 4H or 5M timeframe analysis — only H1 and M15
6. No economic calendar integration — news is not considered
7. No web interface — CLI only
8. No LLM reasoning — decisions are not explained
9. No multi-user support — single instance only
10. Position event write is not always confirmed — telemetry gap under investigation
    (trade record IS written correctly; position_event write may fail silently)

---

## DEVELOPMENT PHASES

Build in this order. Do not skip phases.

**Phase 1: Strategy Engine (standalone, backtestable)**
- Implement all ICT/SMC detectors: OB, FVG, BOS, CHOCH, IFVG, EQH, EQL, SH, SL,
  Liquidity Sweep, Engulfing, Premium/Discount, OTE
- Implement multi-timeframe context builder (4H, 1H, 15M, 5M)
- Implement confluence engine with scoring and tier assignment
- Implement exit planner (SL/TP, partials, trail, breakeven)
- Implement risk evaluator (dynamic lots, fixed lots, drawdown gates)
- Backtest engine: replay M15 bars through the full pipeline
- Target: 500+ unit tests passing, backtest on 2 years EURUSD minimum

**Phase 2: Execution Layer**
- Carry forward D.E.V.I 4.0's tested execution pipeline
- Add retcode handling for all MT5 codes
- Add trailing stop and breakeven executor (modifies live MT5 positions)
- Add partial close executor
- Add position lifecycle tracker (syncs from MT5, detects external closes)
- Full telemetry: all writes confirmed, no silent failures
- Target: all execution paths tested in paper mode

**Phase 3: Bridge Agent**
- Windows service that connects MT5 to cloud via WebSocket
- Installer package (simple double-click setup)
- System tray indicator
- Bridge health monitoring (auto-reconnect on disconnect)
- Security model implementation (token, hardware fingerprint)

**Phase 4: Cloud Server**
- Multi-tenant execution scheduler
- REST API for dashboard
- WebSocket feed for real-time updates
- LLM reasoning layer integration
- Economic calendar service
- Session scheduler per user timezone

**Phase 5: Dashboard**
- Extend divine-earnings-desk.vercel.app
- All pages listed in Web Dashboard Requirements section
- TradingView chart with D.E.V.I overlays
- LLM chat interface
- Bridge connection management

**Phase 6: SaaS Infrastructure**
- User authentication (Clerk or Auth.js)
- Subscription tiers (Stripe integration, matching Divine Earnings pricing)
- Per-user isolation and rate limiting
- Monitoring, alerting, uptime requirements

---

## DELIVERABLES

1. Full source code: bridge agent, cloud server, dashboard, strategy engine
2. Test suite: minimum 400 tests, all passing, coverage > 80%
3. Backtest results: 2-year EURUSD walk-forward, min Sharpe 1.2, max DD < 10%
4. config/retail_default.json — standard retail account config
5. config/ftmo_standard.json — FTMO evaluation config with hard guardrails
6. config/small_account.json — EUR 50-500 account config
7. config/hft_personal.json — HFT mode config for Vantage/StarTrader
8. Docker compose file for cloud server deployment
9. Windows installer for D.E.V.I Bridge Agent
10. docs/architecture.md — system architecture with diagrams
11. docs/operator_guide.md — how to deploy, arm, monitor, and stop the system
12. docs/user_guide.md — dashboard user guide
13. docs/ftmo_guide.md — FTMO evaluation rules, D.E.V.I compliance mode explained
14. docs/strategy.md — complete ICT/SMC methodology with visual examples

---

## BRANDING REQUIREMENTS

- Product name: D.E.V.I (Divine Earnings Virtual Intelligence)
- Brand: Divine Earnings
- Motto: "Where Wisdom Meets Wealth"
- Colours: purple #6F63E7 and black #000000
- Tone: serious, intelligent, transparent, system-driven
- No hype. No guaranteed return language. No "fully automated money machine" copy.
- D.E.V.I communicates like a professional trader who knows what they are doing,
  not like a sales page
- Public claims must distinguish between backtested performance and live performance
  until live verified results exist

---

## SUCCESS CRITERIA

D.E.V.I 5.0 is considered complete when:

1. Backtest on 2-year EURUSD data shows: profit factor >= 1.3, max DD < 10%,
   Sharpe > 1.2, minimum 200 trades in sample
2. FTMO simulation: >= 65% of months pass evaluation constraints
3. All 400+ tests pass
4. Bridge agent connects, relays, and executes without errors on a live MT5 demo
5. First live test: 5 trades on a demo account with correct lot size, SL, TP,
   ticket linkage, and position lifecycle confirmed in logs
6. Dashboard is accessible, charts render, LLM responds to trade queries correctly
7. Multiple test user accounts can run simultaneously without interference

If the backtest does not meet the criteria in point 1, do not proceed to live
execution. Revisit the confluence thresholds and entry conditions first.

---

*D.E.V.I 5.0 — Built on proven foundations. Powered by precision.*
*Divine Earnings | Where Wisdom Meets Wealth*
