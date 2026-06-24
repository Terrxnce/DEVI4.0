# DEVI Cost Model + Broker-Agnostic Design (spec, not yet implemented)

Status: proposal for review. No code changed. Author: design pass 2026-06-23.

## Goal

DEVI should trade any MT5 account the same way, scaled to its size, with correct
economics. Two gaps block this today:

1. Symbol selection is coupled to per-broker symbol names in config
   (`symbol_sessions` keys like `EURUSD.pi`). New broker = new map.
2. Trade economics ignore commission entirely and do not subtract spread from RR,
   so on a commissioned broker DEVI's real RR is lower than it believes.

This doc covers the cost model (gap 2) and how it fits the broker-agnostic
direction (gap 1).

## Where each input actually comes from

| Input | Source | Today |
|---|---|---|
| Symbol list | MT5 `symbols_get` / Market Watch | partial (prefers config basket) |
| Contract specs (tick value, contract size, lot step, min lot, point) | MT5 `symbol_info` | done, auto |
| Live spread | MT5 tick (ask-bid) | fetched in rechecks, used as a gate only |
| Balance / equity / currency / leverage | MT5 `account_info` | balance/equity used |
| Commission per lot | NOT in MT5 reliably | not modeled |
| Prop drawdown rules | NOT in MT5 (firm-side) | per-broker config |

Conclusion: only commission and prop rules need to be supplied. Everything else
is already auto-discoverable. So the manual surface is tiny.

## Part A — Cost model

### A1. Config: a `cost_model` block on the rule template (not per broker)

```
"cost_model": {
  "commission_per_lot_round_turn": 7.0,   // account currency, round turn per 1.0 lot
  "include_spread_in_rr": true,
  "spread_source": "live"                 // live tick; fallback to symbol_info().spread
}
```

Almost every account DEVI will touch charges commission. Reference round-turn FX
rates: FTMO ~$3, Blueberry ~$7, Vantage raw ~$6, Vantage Pro ~$3. A pure
market-maker / "commission-free" account uses 0 here but hides the cost in a wider
spread instead (caught by the live-spread term). Commission is a property of the
account/template the operator selects, because MT5 does not expose it reliably.

NOTE: FTMO is NOT free. The earlier assumption that FTMO cost was "in the spread"
was wrong — it is raw spread + ~$3/lot commission. DEVI has been ignoring
commission on every broker, including FTMO, so its RR has always been slightly
optimistic. The cost model is a universal correction, not a Blueberry-only patch.

### A2. Where it hooks: the RR gate in the exit planner

Today RR is price/point based: `RR = tp_distance / sl_distance`. The change is to
express costs in points and net them out before comparing to `min_rr`:

```
commission_pts = commission_per_lot_round_turn / point_value_per_lot
spread_pts     = live_spread_in_points            (entry crossing cost)
total_cost_pts = commission_pts + spread_pts

net_reward_pts = tp_distance_pts - total_cost_pts
net_risk_pts   = sl_distance_pts + total_cost_pts
net_RR         = net_reward_pts / net_risk_pts

accept if net_RR >= min_rr
```

Key property: all terms scale linearly with lot, so net_RR is lot-independent —
it can be computed at the point level without knowing the lot, fitting the
existing point-based RR math. point_value_per_lot already comes from the MT5
profile (`tick_value * point / tick_size`).

### A3. Effect

- The `min_rr` floor (1.2) becomes a NET 1.2 instead of a gross one.
- High-RR trades (2.0+) barely move; the low-RR tail (1.2-1.4) on a commissioned
  broker correctly fails the net test. This is the exact band that quietly
  turned negative on the 10k Blueberry eval.
- FTMO (commission 0) behavior is unchanged — parity preserved.

### A4. Realized side: log costs from the deal

The MT5 deal carries commission and swap. Capture both in the trade-close /
trade_partial_close records (alongside the partial-leg PnL fix) so post-hoc
expectancy is net, not gross. Today the ledger logs gross profit and is blind to
what costs ate.

### A5. Tests

- Zero-cost config: net_RR == gross RR (FTMO parity).
- $7/lot commission: a 1.25 gross trade drops below 1.2 net -> rejected.
- net_RR is invariant to lot size.
- Spread pulled live; falls back to symbol_info().spread when tick missing.
- Deal commission + swap appear in the close ledger.

## Part B — Broker-agnostic recognition (the bigger change)

### B1. Replace per-broker symbol maps with a normalizer

- Read tradable symbols from MT5 Market Watch (`symbols_get`) instead of a
  hardcoded config basket.
- Normalize each to a canonical base by stripping known suffixes
  (`.pi`, `.raw`, `.pro`, `.cash`, `.p`, trailing `+ m c`), e.g.
  `EURUSD.pi -> EURUSD`, `NAS100.p -> NAS100`.
- Key sessions, detectors, and correlation logic off the canonical base.
- Keep the broker's real symbol string only for order placement.

One normalizer replaces every per-broker `symbol_sessions` map.

### B2. Rule templates, selected per account (not per broker)

Drawdown rules and commission cannot be auto-detected. Provide a small library of
templates and have the operator pick one at connect time:

- `ftmo_2step` (daily 5% / total 10%, reset Prague midnight)
- `prop_1step` (daily 4% / total 6%)
- `live_no_prop` (no drawdown gate; live broker, real money)

Each template carries its `cost_model`. A handful of templates cover the whole
market DEVI targets, versus one config per firm. Trailing-drawdown firms are
explicitly OUT of scope — DEVI does not and will not chase trailing-DD rules.

### B3. What this delivers

Connect any MT5 account -> DEVI reads balance, symbols, and specs automatically,
normalizes the symbols, applies the selected rule template, and trades the same
logic scaled to the account. No new broker profile required. The only manual
step is choosing the rule template (or "live, no prop") once per account.

### B4. Honest caveats

- Some brokers hide symbols until added to Market Watch; discovery must add/enable
  them or the operator must.
- Small accounts (<~2k) will silently shed wide-stop FX, gold, and indices to the
  20% risk-deviation reject. That is correct behavior, but the basket narrows;
  it is not "identical to the 100k," just the same logic on a smaller universe.
- Trailing-drawdown prop firms are out of scope by decision, not omission.

## Suggested sequencing

1. Cost model (Part A) — contained, high value, testable, makes economics honest.
2. Symbol normalizer (B1) — unlocks any-broker symbol handling.
3. Rule templates + connect-time selection (B2) — replaces broker_profiles.
```
