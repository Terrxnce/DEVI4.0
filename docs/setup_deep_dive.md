# DEVI Setup Deep Dive — why OB_WITH_FVG fails, and how to fix the engine

Grounded in: the 2-year / 8,232-setup backtest, DEVI's actual detector and
decision code, and how these setups are traded profitably (SMC/ICT literature).

## The 2-year verdict (per setup, R-multiples, before costs)

| Setup | Trades | Win% | Expectancy | Read |
|---|---|---|---|---|
| SWEEP_REVERSAL_BEAR | 71 | 39.4% | +0.350R | best edge, rare |
| SWEEP_REVERSAL_BULL | 55 | 40.7% | +0.200R | best edge, rare |
| OB_WITH_BOS | 3073 | 34.7% | +0.077R | profitable workhorse |
| JUDAS_WITH_OB | 421 | 32.2% | +0.063R | modest, positive |
| SWEEP_WITH_OB | 1047 | 32.3% | -0.012R | ~breakeven |
| OB_WITH_FVG | 3565 | 30.3% | -0.077R | the drag |

System overall: 32.4% win, +0.001R — breakeven before costs, i.e. **negative
after commission**. Regime split did NOT generalize (RANGING +0.042R, NEUTRAL
-0.009R) — drop the regime-filter idea.

## The systemic root cause (biggest finding, lifts EVERY setup)

DEVI enters when two things are true: the structures are detected, and current
price is *within proximity* of the order-block zone (`entry_gate.py` — "ensures
DEVI only enters on a genuine retest of the zone"). That is a **proximity**
check, not a **confirmation** check.

Every profitable SMC source says the same thing: do NOT enter on proximity to
the zone. Wait for price to RETURN to the zone, then require a lower-timeframe
**Market Structure Shift / Change of Character (CHoCH) or a rejection** that
proves the zone is reacting — *then* enter. "Entering blindly at a confluence
zone is risky. Wait for confirmation: rejection candles, liquidity sweeps, or
lower-timeframe break of structure." Strict-confirmation SMC backtests report
50-65% win rates; DEVI sits at 32%. The gap is confirmation-on-return.

**DEVI already has the tool and isn't using it for entries.** There is a CHoCH
detector at `src/narrative/choch_detector.py`, but it only feeds the narrative/
commentary layer — it does not gate the entry decision. Wiring CHoCH/MSS
confirmation into the entry gate is the single highest-leverage change in this
whole document. It would raise the win rate across ALL setups, which is exactly
what a system-wide 32% / breakeven result points to: DEVI is a *formation +
proximity* detector, while profitable SMC is a *retest + confirmation* model.

## Per-setup deep dive

### OB_WITH_FVG  (-0.077R, your most common setup) — why it fails

Three compounding reasons, code + literature:

1. **The OB and the FVG are usually the SAME impulse — not independent
   confluence.** An order block forms from an impulsive move; that same impulse
   prints the fair value gap right next to it. The literature is explicit: "Both
   are generated from the same price action." DEVI's `match_setup_candidates`
   pairs an OB with an FVG even when they're from one move (it only skips a pair
   if same TYPE and same bar). So OB_WITH_FVG frequently counts one event as two
   confirmations, inflating the confidence tier without adding real information.
   OB_WITH_BOS doesn't have this problem — a BOS is a genuinely separate
   structural event.

2. **FVG is used as a tier-boosting confirmation, when its real job is to
   QUALIFY the OB.** Proper use: "an order block without an associated FVG is
   much weaker — the FVG confirms the impulse was strong enough to be a real
   OB." So FVG should be a *filter* on OB quality, not an independent vote that
   bumps OB_WITH_FVG to Tier A. DEVI has it backwards.

3. **Permissive detection.** The FVG detector accepts any gap >= 0.3x ATR
   (`min_gap_atr_mult: 0.3`). Small, weak gaps qualify — noise, and exactly the
   gaps institutions use to trap retail with false fills.

4. **The systemic no-confirmation problem hits this setup hardest**, because the
   FVG gives false confidence to enter into a zone that hasn't yet proven it
   reacts.

### OB_WITH_BOS  (+0.077R) — why it works

The BOS is an *independent, directional* structural event: price actually broke
a prior swing in the trade's direction. So OB + BOS is real two-event confluence
(supply/demand zone + confirmed momentum), not one event double-counted. This is
the profitable core — protect it.

### SWEEP_REVERSAL_BULL/BEAR  (+0.20 / +0.35R) — your best edge, underused

Liquidity grab then reversal is, per the literature, the highest-probability SMC
pattern — it has *built-in* confirmation (the sweep is the trap + reaction). Your
data agrees: best expectancy by a wide margin. But only 126 trades in 2 years —
DEVI barely takes them. This is upside, not damage control.

### SWEEP_WITH_OB  (-0.012R) — the OB requirement dilutes the sweep

Note the contrast: pure SWEEP_REVERSAL is +0.2-0.35R, but bolting an OB
requirement onto a sweep drags it to breakeven. The OB gate is filtering out the
clean reversals and/or delaying entry. The sweep itself is the edge; the OB is
diluting it.

### JUDAS_WITH_OB  (+0.063R) — positive, can be sharper

Judas (London fake-move that sweeps the Asian range and reverses) is cited as
"the highest probability setup in the entire toolkit" — but only when you trade
it WITH a daily directional bias. DEVI's judas may fire without a strong bias
filter. Adding a daily-bias requirement should lift it toward the sweep-reversal
numbers.

### OB_WITH_ENGULFING / REJECTION_WITH_FVG — too rare to read

Did not appear in meaningful volume in the 2-year run. Park them.

## How to improve (ranked, every one testable with tools/backtest.py)

1. **Wire CHoCH/MSS confirmation into the entry gate.** Use the existing
   `choch_detector.py`: after price returns to the zone, require a lower-TF CHoCH
   or rejection in the trade direction before entering. Highest leverage — lifts
   every setup. This is the difference between 32% and the 50-65% strict-SMC
   numbers.

2. **Reclassify FVG: filter, not confirmation.** Require an OB to have an
   associated FVG to be high quality (FVG validates the OB), and STOP counting
   FVG as an independent confluence vote. If kept as confluence, require the FVG
   to be independent of the OB (separated by N bars).

3. **Tighten FVG detection.** `min_gap_atr_mult` 0.3 -> 0.5, weight displacement
   higher. Kills noise/trap gaps.

4. **Lean into sweep/judas reversals.** Allow pure SWEEP_REVERSAL more freely;
   add a daily-bias filter to JUDAS. Don't dilute clean reversals with an OB gate.

5. **Drop or gate OB_WITH_FVG** until 1-3 are done. The backtest says removing it
   takes the basket from +0.001R to ~+0.06R immediately.

## New setups worth adding

- **CHoCH reversal** — the change-of-character reversal entry. DEVI has the
  detector; it just needs a setup class and to be on the entry path, not only the
  narrative.
- **Unicorn model (Breaker block + FVG overlap)** — a breaker (failed OB) that
  overlaps an FVG is a well-documented high-probability reversal POI.
- **Sweep -> CHoCH -> FVG (the full stack)** — sweep liquidity, confirm with a
  CHoCH, enter on the resulting FVG. This is the combination the strict-SMC 50-65%
  win-rate backtests use. It bundles your best edge (sweep) with confirmation.

## Experiment order

1. Drop OB_WITH_FVG -> confirm basket moves to ~+0.06R (baseline).
2. Add CHoCH/MSS entry confirmation -> re-run ALL setups; expect win-rate lift
   across the board. (The big one.)
3. Tighten FVG detection + reclassify as filter -> does OB+FVG recover edge?
4. Loosen sweep/judas reversals + daily bias -> capture more of the best edge.

Reminder: R-multiples here exclude commission. A setup must clear roughly
+0.05-0.08R just to break even after costs, so "slightly positive" in this doc is
"still losing live." The bar is higher than zero. The backtest is now the loop:
change one thing, re-run, read the number — before anything touches the live
account.
