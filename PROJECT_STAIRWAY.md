# PROJECT STAIRWAY

**The Compounding Capital Strategy at the Heart of LeoBook**

> LeoBook Intelligence Framework · Materialless LLC · 2026
> Status: Active Development · Not yet publicly available

---

## 1. Why LeoBook Must Exist

Every day, across leagues on every continent, 100 to 500 football matches are played — and on weekends, that number climbs even higher. Each match is a data event: form, history, standings, odds, and momentum all collide into a predictable signal that most bettors never see clearly because they are looking at one or two matches at a time.

LeoBook was built on a different premise: what if a system could scan every match available on a given day, run each through a trained intelligence engine, and surface only those outcomes with genuine predictive confidence — then compound the returns from those selections systematically?

That premise is Project Stairway.

### The Core Conviction

- **Volume**: The sheer number of daily matches creates a rich selection environment. You do not need every match — you need the right ones.
- **Edge**: A prediction system with a true probabilistic edge over the bookmaker — even a modest one — generates long-run value when applied consistently.
- **Compounding**: Small, confident wins reinvested become exponential over a short series of steps.

---

## 2. The Stairway Structure

The staircase is a **7-step compounding sequence**, starting from a fixed base seed:

| Step | Stake | Odds Target | Payout | Cumulative Return |
|------|-------|-------------|--------|-------------------|
| 1 | ₦1,000 | ~4.0 | ₦4,000 | ₦4,000 |
| 2 | ₦4,000 | ~4.0 | ₦16,000 | ₦16,000 |
| 3 | ₦16,000 | ~4.0 | ₦64,000 | ₦64,000 |
| 4 | ₦64,000 | ~4.0 | ₦256,000 | ₦256,000 |
| 5 | ₦256,000 | ~4.0 | ₦1,024,000 | ₦1,024,000 |
| 6 | ₦1,024,000 | ~4.0 | ₦4,096,000 | ₦4,096,000 |
| 7 | ₦2,048,000 | ~4.0 | ₦2,187,000 (net) | ₦2,187,000 |

### Rules

1. **Start**: Every cycle begins with exactly ₦1,000.
2. **Win**: The full payout rolls into the next step's stake.
3. **Loss at any step**: The cycle resets to Step 1 with a fresh ₦1,000. Maximum loss per cycle: ₦1,000.
4. **Completion**: A full 7-step winning streak produces ~₦2,186,000 net profit from a ₦1,000 seed.

---

## 3. The Mathematics — Honest and Complete

### 3.1 The Correct Framing of Accuracy

A common intuition in sports prediction is that selecting a small number of matches from a large pool somehow transfers the pool's aggregate accuracy to the selection. This is mathematically incorrect and must be stated clearly.

**The Correct Statement**: LeoBook scans 100–500 matches daily and surfaces only those where the prediction model's estimated true probability significantly exceeds the bookmaker's implied probability. The accuracy that matters is not the pool's aggregate accuracy — it is the **per-step win probability** of the specific bets LeoBook selects.

If a bookmaker prices a match at 4.0 odds, they imply a 25% win probability. If LeoBook's model estimates the true probability at 38%, that 13-percentage-point gap is the edge. That edge — consistently identified and consistently positive — is what drives long-run value.

### 3.2 Theoretical Win-Streak Probabilities

| Per-Step Win Prob. (p) | 7-Win Streak Prob. | Classification | Context |
|--|--|--|--|
| 25% (fair odds, no edge) | ~0.006% | Baseline / No Edge | Bookmaker's implied probability at 4.0 |
| 28% | ~0.028% | Marginal Edge | Slight model advantage |
| 32% | ~0.039% | Good Edge | Solid prediction system |
| 35% | ~0.064% | Strong Edge | Well-calibrated model |
| 40% | ~0.164% | Excellent Edge | High-performing system |
| 45% | ~0.373% | Exceptional Edge | World-class calibration |

### 3.3 Variance — The Honest Reality

- **Cycles will fail more often than they succeed.** This is expected and is not a signal of system failure.
- **The system's value is realised over many cycles** — the expected return across N cycles is what matters, not any single cycle.
- **The ₦1,000 reset is not a punishment — it is a feature.** It caps every cycle's loss and keeps the system running.
- **Patience and volume are structural requirements** of the strategy, not optional virtues.

### The Asymmetry That Drives the Vision

- **Worst case per cycle**: ₦1,000 lost. Cycle resets.
- **Best case per cycle**: ₦2,186,000 net profit from a ₦1,000 seed.
- **The ratio**: 2,186:1 return-to-risk per completed staircase.
- **The thesis**: The more matches LeoBook can scan, and the stronger the model's edge, the more often a cycle completes — and the ratio never changes.

---

## 4. Scale — Why Sports Plurality Matters

Football is the starting point, not the ceiling. The principle underlying Project Stairway is not football-specific — it is a function of match volume and prediction edge. Every sport that can be modelled, predicted, and placed on Football.com (or equivalent platforms) is a candidate for the staircase.

More sports means:
- More daily matches to scan for high-confidence selections.
- More diverse statistical environments for the model to learn from.
- More opportunities for the cycle to find its 7-step winning run.
- Reduced dependency on any single league, season, or sport's form patterns.

The long-term architecture of LeoBook is designed with this plurality in mind. The RL model's SharedTrunk + LoRA adapter design allows sport-specific and league-specific fine-tuning without rebuilding the core intelligence.

> **The Vision in One Sentence**: A system that sees every match, identifies every edge, and compounds every win — at scale, across sports, starting from ₦1,000.

---

## 5. Open Quests — What Pipeline Testing Will Reveal

LeoBook is in active development. The following are not gaps or weaknesses — they are the questions the system is being built to answer. When the data is available, this section will be updated with measured results.

1. What is LeoBook's actual per-step win probability on its selected bets at ~4.0 odds?
2. How does per-step win probability vary by league, sport, and season stage?
3. Is the optimal step target a single match at 4.0, or a 2–3 match accumulator that multiplies to 4.0?
4. What is the empirically observed calibration of the model?
5. What is the expected number of cycles before a full 7-step staircase completes?
6. How does the staircase perform when applied simultaneously across multiple sports?
7. What is the optimal confidence threshold gate for bet placement?
8. At what per-step win rate does Project Stairway become net-positive over 100 cycles?

These questions will be answered by data, not assumption.

---

## 6. What Project Stairway Is Not

- **Not a guaranteed profit system.** No betting system can guarantee profit. The staircase is designed to extract value from a genuine prediction edge — without that edge, the math does not work in the long run.
- **Not a commercial product.** Project Stairway operates within the context of Football.com's platform and its terms of service. This is a personal capital management strategy, not a service offered to third parties.
- **Not a claim of 95% accuracy.** No per-match or per-combo accuracy claim is made in this document. The system's accuracy is an open quest.
- **Not reckless.** The ₦1,000 base seed, the hard reset on loss, the confidence threshold gate, the audit logging, the dry-run testing mode — these are deliberate engineering decisions that treat capital preservation as seriously as capital growth.

---

## 7. The Mission

> What is the probability that, out of 100–500 thoroughly analysed matches,
> a system can find 7 predictions in a row that are right?
> And what happens to ₦1,000 if it can?

The mathematical answer is: rare, but non-zero — and the rarity is precisely what makes the 2,186:1 return-to-risk ratio possible. The engineering answer is: build the best prediction system available, give it the richest data, train it on history, gate it on confidence, and run the staircase with discipline.

That is what LeoBook is. That is why it must exist.

If it fails — learn, improve, and move forward. If it succeeds — the outcome is life-changing. The risk is ₦1,000. The quest is everything beyond it.

---

**Document Status**
- Version: 1.0 — Initial Publication
- System Status: Active Development — Modular Testing Phase
- Performance Data: Pending — Full Pipeline Testing
- Next Update: Upon completion of first full end-to-end pipeline test
- Classification: Internal Development Document — Not for public distribution
