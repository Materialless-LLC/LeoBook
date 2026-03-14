# trainer.py: PPO-based RL trainer with chronological training and composite reward.
# Part of LeoBook Core — Intelligence (RL Engine)
#
# Classes: RLTrainer
# Called by: Leo.py (--train-rl)

"""
RL Trainer Module
Handles offline training from historical fixtures (chronological, day-by-day)
and online updates from new prediction outcomes.

Training constraints:
- Strict chronological order (no future data leakage)
- Season-aware date selection: training starts from each league's actual season
  start date, not a global hardcoded date floor
- Last-10 matches prioritized via recency weighting
- Prediction accuracy is the primary reward signal

Season targeting (--train-season CLI flag):
  "current"     Use each league's current_season (from leagues table). Default.
  "all"         All available seasons, oldest-first. Use for a full cold retraining.
  N (int)       Past season by offset: 1 = most recent past, 2 = two seasons ago, etc.
                Matches the --season N convention used by enrich_leagues.
  "2024/2025"   Explicit season label (split-season format).
  "2025"        Explicit season label (calendar-year format).
"""

import os
import re
import json
import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F   # ADDED for proper KL imitation loss
from typing import Dict, Any, List, Optional, Tuple, Union
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

from .model import LeoBookRLModel
from .feature_encoder import FeatureEncoder
from .adapter_registry import AdapterRegistry
from .market_space import (
    ACTIONS, N_ACTIONS, SYNTHETIC_ODDS, STAIRWAY_BETTABLE,
    compute_poisson_probs, probs_to_tensor_30dim,
    derive_ground_truth, stairway_gate, check_phase_readiness,
    PHASE2_MIN_ODDS_ROWS, PHASE2_MIN_DAYS_LIVE,
    PHASE3_MIN_ODDS_ROWS, PHASE3_MIN_DAYS_LIVE,
)

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
MODELS_DIR = PROJECT_ROOT / "Data" / "Store" / "models"

# Paths
BASE_MODEL_PATH = MODELS_DIR / "leobook_base.pth"
TRAINING_CONFIG_PATH = MODELS_DIR / "training_config.json"


class RLTrainer:
    """
    PPO-based trainer for the LeoBook RL model.

    Training proceeds chronologically day-by-day:
    For each day D:
        1. Build features using ONLY data before D (season-aware window)
        2. For each fixture on day D:
            a. Encode features
            b. Model predicts action (market + stake)
            c. After outcome: compute composite reward
            d. PPO gradient update
    """

    def __init__(
        self,
        lr_base: float = 5e-5,
        lr_league: float = 1e-4,
        lr_team: float = 5e-5,
        gamma: float = 0.99,
        clip_epsilon: float = 0.2,
        max_seasons_back: int = 2,
        device: str = "cpu",
    ):
        self.device = torch.device(device)
        self.max_seasons_back = max_seasons_back
        self.gamma = gamma
        self.clip_epsilon = clip_epsilon

        # Model & registry
        self.model = LeoBookRLModel().to(self.device)
        self.registry = AdapterRegistry()

        # Optimizer with per-component learning rates
        param_groups = [
            {"params": self.model.trunk.parameters(), "lr": lr_base},
            {"params": self.model.policy_head.parameters(), "lr": lr_base},
            {"params": self.model.value_head.parameters(), "lr": lr_base},
            {"params": self.model.stake_head.parameters(), "lr": lr_base},
            {"params": self.model.league_embedding.parameters(), "lr": lr_league},
            {"params": self.model.league_adapters.parameters(), "lr": lr_league},
            {"params": self.model.team_adapters.parameters(), "lr": lr_team},
        ]
        self.optimizer = optim.AdamW(param_groups, weight_decay=1e-4)
        self.scheduler = optim.lr_scheduler.CosineAnnealingWarmRestarts(
            self.optimizer, T_0=1000, T_mult=2
        )

        self._step_count = 0
        self.kl_weight = 0.1  # ADDED: starts at 0.1, anneals to 0 in Phase 2+ so RL can outperform expert

    # -------------------------------------------------------------------
    # Season Discovery & Date Selection
    # -------------------------------------------------------------------

    def _discover_seasons(self, conn) -> List[str]:
        """
        Return all distinct season labels found in schedules, most-recent-first.

        Sorts by the 4-digit start year embedded in the season string so both
        split-season ("2024/2025") and calendar-year ("2025") formats rank correctly.
        """
        rows = conn.execute(
            "SELECT DISTINCT season FROM schedules "
            "WHERE season IS NOT NULL AND season != ''"
        ).fetchall()
        seasons = [r[0] for r in rows]

        def _start_year(s: str) -> int:
            m = re.match(r'(\d{4})', s)
            return int(m.group(1)) if m else 0

        return sorted(seasons, key=_start_year, reverse=True)

    def _get_season_dates(
        self, conn, target_season: Union[str, int] = "current"
    ) -> Tuple[List[str], str]:
        """
        Build the ordered list of fixture-dates for training, filtered to the
        requested season scope.

        Args:
            target_season:
                "current"   — per-league join against leagues.current_season.
                              Starts training from the actual start of each
                              league's live season. Default.
                "all"       — all available completed fixtures, oldest-first.
                              Use for a full cold retraining across all seasons.
                int N       — past season by offset: 1 = most recent past,
                              2 = two seasons ago, etc. (0-indexed internally,
                              1-indexed in the CLI to match enrich_leagues).
                str label   — explicit season label, e.g. "2024/2025" or "2025".

        Returns:
            (dates, label) where dates is a chronologically sorted list of
            date strings and label is a human-readable description for logging.
        """
        today_str = datetime.now().strftime("%Y-%m-%d")

        # ── All seasons ─────────────────────────────────────────────────────────
        if target_season == "all":
            rows = conn.execute("""
                SELECT DISTINCT date FROM schedules
                WHERE date IS NOT NULL
                  AND home_score IS NOT NULL AND away_score IS NOT NULL
                  AND date <= ?
                ORDER BY date ASC
            """, (today_str,)).fetchall()
            return [r[0] for r in rows], "all seasons (oldest → newest)"

        # ── Past season by offset (int) ──────────────────────────────────────────
        if isinstance(target_season, int) and target_season >= 1:
            seasons = self._discover_seasons(conn)
            # Index 0 = current/latest season label; index N = past season N
            if target_season >= len(seasons):
                print(f"  [TRAIN] Season offset {target_season} out of range "
                      f"({len(seasons)} seasons in DB). Falling back to current.")
            else:
                season_label = seasons[target_season]  # 1-indexed offset
                rows = conn.execute("""
                    SELECT DISTINCT date FROM schedules
                    WHERE season = ?
                      AND home_score IS NOT NULL AND away_score IS NOT NULL
                      AND date IS NOT NULL AND date <= ?
                    ORDER BY date ASC
                """, (season_label, today_str)).fetchall()
                if rows:
                    return [r[0] for r in rows], f"season {season_label} (past offset {target_season})"
                print(f"  [TRAIN] Season '{season_label}' has no completed fixtures. "
                      f"Falling back to current.")

        # ── Explicit season label (non-"current" string) ─────────────────────────
        if isinstance(target_season, str) and target_season != "current":
            rows = conn.execute("""
                SELECT DISTINCT date FROM schedules
                WHERE season = ?
                  AND home_score IS NOT NULL AND away_score IS NOT NULL
                  AND date IS NOT NULL AND date <= ?
                ORDER BY date ASC
            """, (target_season, today_str)).fetchall()
            if rows:
                return [r[0] for r in rows], f"season {target_season}"
            print(f"  [TRAIN] Season '{target_season}' not found or has no completed "
                  f"fixtures. Falling back to current.")

        # ── Current season (default) ─────────────────────────────────────────────
        # Join schedules against leagues.current_season so training starts from
        # each league's actual season start date rather than a global date floor.
        #
        # VALID_SEASON_FROM_YEAR guards against stale leagues.current_season values.
        # Some leagues have current_season set to "2012", "2013/2014" etc. from
        # incomplete enrichment runs. These match old historical data and cause the
        # training window to span back to 2012. We reject any season whose 4-digit
        # start year is before VALID_SEASON_FROM_YEAR.
        # Split-season "2024/2025" → start year 2024 ✓
        # Calendar-year "2025" or "2026" → start year 2025/2026 ✓
        # Stale "2012" or "2013/2014" → start year 2012/2013 ✗ rejected
        VALID_SEASON_FROM_YEAR = 2023

        rows = conn.execute("""
            SELECT DISTINCT s.date
            FROM schedules s
            INNER JOIN leagues l ON s.league_id = l.league_id
            WHERE s.season = l.current_season
              AND s.home_score IS NOT NULL AND s.away_score IS NOT NULL
              AND s.date IS NOT NULL AND s.date <= ?
              AND CAST(SUBSTR(l.current_season, 1, 4) AS INTEGER) >= ?
            ORDER BY s.date ASC
        """, (today_str, VALID_SEASON_FROM_YEAR)).fetchall()
        dates = [r[0] for r in rows]

        if dates:
            return dates, f"current season (per-league season join, seasons >= {VALID_SEASON_FROM_YEAR})"

        # First fallback: current-season join returned nothing even with year filter.
        # leagues.current_season is either empty or all stale.
        # Use the most recent season label from schedules that is recent enough.
        seasons = self._discover_seasons(conn)
        recent_seasons = [
            s for s in seasons
            if re.match(r'(\d{4})', s)
            and int(re.match(r'(\d{4})', s).group(1)) >= VALID_SEASON_FROM_YEAR
        ]
        if recent_seasons:
            season_label = recent_seasons[0]  # most recent valid season
            print(
                f"\n  [TRAIN] ⚠ Current-season join returned no valid recent dates.\n"
                f"  [TRAIN]   leagues.current_season may be stale or unpopulated.\n"
                f"  [TRAIN]   Falling back to most recent season in schedules: {season_label}\n"
                f"  [TRAIN]   → Run: python Leo.py --enrich-leagues  to fix this.\n"
            )
            rows = conn.execute("""
                SELECT DISTINCT date FROM schedules
                WHERE season = ?
                  AND home_score IS NOT NULL AND away_score IS NOT NULL
                  AND date IS NOT NULL AND date <= ?
                ORDER BY date ASC
            """, (season_label, today_str)).fetchall()
            dates = [r[0] for r in rows]
            if dates:
                return dates, f"season {season_label} (fallback — run --enrich-leagues to fix current_season)"

        # Last resort: abort cleanly. Do NOT silently train on all history.
        print(
            f"\n  [TRAIN] ✗ CRITICAL: No recent season data found (seasons >= {VALID_SEASON_FROM_YEAR}).\n"
            f"  [TRAIN]   Training aborted to prevent spanning all historical data.\n"
            f"\n"
            f"  [TRAIN]   Fix: python Leo.py --enrich-leagues\n"
            f"  [TRAIN]   Then retry: python Leo.py --train-rl\n"
            f"\n"
            f"  [TRAIN]   To train on ALL history intentionally:\n"
            f"  [TRAIN]   python Leo.py --train-rl --train-season all --cold\n"
        )
        return [], "aborted — no recent season metadata (run --enrich-leagues first)"

    # -------------------------------------------------------------------
    # Reward functions (30-dim action space)
    # -------------------------------------------------------------------

    @staticmethod
    def _get_correct_actions(outcome: Dict[str, Any]) -> set:
        """Map actual outcome to the set of correct action indices (30-dim)."""
        home_score = outcome.get("home_score", 0)
        away_score = outcome.get("away_score", 0)
        gt = derive_ground_truth(int(home_score), int(away_score))
        correct = set()
        for action in ACTIONS:
            key = action["key"]
            if gt.get(key) is True:
                correct.add(action["idx"])
        return correct

    @staticmethod
    def _compute_phase1_reward(
        chosen_action_idx: int,
        home_score: int,
        away_score: int,
    ) -> float:
        """
        Phase 1 reward: accuracy-based (no odds data yet).
        Correct prediction of bettable market = +1.0
        Correct prediction of non-bettable = +0.3
        Wrong prediction = -0.5
        no_bet when good bets existed = -0.2
        no_bet when all markets low confidence = +0.1
        """
        action = ACTIONS[chosen_action_idx]
        key = action["key"]
        gt = derive_ground_truth(int(home_score), int(away_score))

        if key == "no_bet":
            any_bettable_correct = any(
                gt.get(ACTIONS[i]["key"], False) is True
                for i in STAIRWAY_BETTABLE
            )
            return -0.2 if any_bettable_correct else +0.1

        outcome = gt.get(key)
        if outcome is None:
            return 0.0

        bettable, _ = stairway_gate(key)
        if outcome is True:
            return 1.0 if bettable else 0.3
        else:
            return -0.5

    @staticmethod
    def _compute_phase2_reward(
        chosen_action_idx: int,
        home_score: int,
        away_score: int,
        live_odds: Optional[float] = None,
        model_prob: Optional[float] = None,
    ) -> float:
        """
        Phase 2 reward: value-based (real odds available).
        """
        action = ACTIONS[chosen_action_idx]
        key = action["key"]
        gt = derive_ground_truth(int(home_score), int(away_score))

        if key == "no_bet":
            any_value_bet_missed = any(
                gt.get(ACTIONS[i]["key"], False) is True
                for i in STAIRWAY_BETTABLE
                if SYNTHETIC_ODDS.get(ACTIONS[i]["key"], 0) >= 1.30
            )
            return -0.3 if any_value_bet_missed else +0.1

        bettable, reason = stairway_gate(key, live_odds, model_prob)
        if not bettable:
            return -0.1

        outcome = gt.get(key)
        if outcome is None:
            return 0.0

        odds = live_odds if live_odds else SYNTHETIC_ODDS.get(key, 1.5)
        if outcome is True:
            return odds - 1.0   # profit
        else:
            return -1.0          # loss

    # -------------------------------------------------------------------
    # Training step (PPO)
    # -------------------------------------------------------------------

    # -------------------------------------------------------------------
    # KL Blending (Rule Engine -> Prob Distribution)
    # -------------------------------------------------------------------

    def _get_rule_engine_probs(self, vision_data: Dict[str, Any]) -> torch.Tensor:
        """
        Phase 1 expert signal: Poisson probability distribution
        over 30-dim action space, derived from match-specific xG.

        Computes xG from the team's actual form data, then runs
        Poisson to get per-market probabilities. Blends with
        RuleEngine raw_scores for 1X2 markets (40/60 weight).

        Returns: torch.Tensor shape (30,) summing to 1.0
        """
        # 1. Compute match-specific xG from form data
        h2h = vision_data.get("h2h_data", {})
        home_form = [m for m in h2h.get("home_last_10_matches", []) if m][:10]
        away_form = [m for m in h2h.get("away_last_10_matches", []) if m][:10]
        home_team = h2h.get("home_team", "")
        away_team = h2h.get("away_team", "")

        xg_home = FeatureEncoder._compute_xg(home_form, home_team, is_home=True)
        xg_away = FeatureEncoder._compute_xg(away_form, away_team, is_home=False)

        # 2. Get Rule Engine raw_scores for 1X2 blending
        raw_scores = None
        try:
            from ..rule_engine import RuleEngine
            analysis = RuleEngine.analyze(vision_data)
            if analysis.get("type") != "SKIP":
                raw_scores = analysis.get("raw_scores")
        except Exception:
            pass

        # 3. Compute Poisson probabilities for all 30 markets
        probs = compute_poisson_probs(xg_home, xg_away, raw_scores)

        # 4. Apply synthetic stairway weight
        for action in ACTIONS:
            key = action["key"]
            if key == "no_bet":
                continue
            bettable, _ = stairway_gate(key)
            if not bettable:
                probs[key] *= 0.3

        # 5. Convert to ordered tensor
        vec = probs_to_tensor_30dim(probs)
        tensor = torch.tensor(vec, dtype=torch.float32)

        # 6. Safety: if all near-zero, return uniform
        if tensor.sum() < 0.1:
            return torch.ones(N_ACTIONS, dtype=torch.float32).to(self.device) / N_ACTIONS

        return (tensor / tensor.sum()).to(self.device)

    def train_step(
        self,
        features: torch.Tensor,
        league_idx: int,
        home_team_idx: int,
        away_team_idx: int,
        outcome: Optional[Dict[str, Any]] = None,
        expert_probs: Optional[torch.Tensor] = None,
        use_kl: bool = False,
    ) -> Dict[str, float]:
        """
        Single training step for Phase 1 (Imitation) or Phase 2/3 (PPO).
        """
        self.model.train()
        features = features.to(self.device)

        # Forward pass
        policy_logits, value, stake = self.model(
            features, league_idx, home_team_idx, away_team_idx
        )
        action_probs = torch.softmax(policy_logits, dim=-1)

        total_loss = torch.tensor(0.0, device=self.device)
        metrics = {}

        if expert_probs is not None and outcome is None:
            # --- Phase 1: Imitation Learning ---
            if expert_probs.dim() == 1:
                expert_probs = expert_probs.unsqueeze(0)
            # FIXED: Proper KL divergence for soft expert probabilities 
            # (was broken cross_entropy — root cause of 650+ days with no learning)
            policy_log_probs = F.log_softmax(policy_logits, dim=-1)
            imitation_loss = F.kl_div(policy_log_probs, expert_probs, reduction='batchmean')

            # KL divergence as monitoring metric + regulariser
            kl_div = torch.sum(
                expert_probs * (torch.log(expert_probs + 1e-10) - torch.log(action_probs + 1e-10))
            )
            total_loss = imitation_loss + self.kl_weight * kl_div

            rl_action = torch.argmax(action_probs, dim=-1).item()
            metrics["imitation_loss"] = imitation_loss.item()
            metrics["kl_div"] = kl_div.item()
            metrics["action"] = rl_action
            metrics["rule_engine_acc"] = 1.0 if rl_action == torch.argmax(expert_probs).item() else 0.0
            metrics["max_prob"] = action_probs.max().item()  # ADDED for convergence visibility

        # --- B. RL / PPO (Phase 2 & 3) ---
        elif outcome is not None:
            dist = torch.distributions.Categorical(action_probs)
            action = dist.sample()
            log_prob = dist.log_prob(action)

            h_score = outcome.get("home_score", 0)
            a_score = outcome.get("away_score", 0)
            active_phase = getattr(self, 'active_phase', 1)

            if active_phase >= 2:
                reward = self._compute_phase2_reward(action.item(), h_score, a_score)
            else:
                reward = self._compute_phase1_reward(action.item(), h_score, a_score)

            reward_tensor = torch.tensor([reward], dtype=torch.float32, device=self.device)
            advantage = reward_tensor - value.squeeze(-1)

            ratio = torch.exp(log_prob - log_prob.detach())
            clipped = torch.clamp(ratio, 1.0 - self.clip_epsilon, 1.0 + self.clip_epsilon)
            policy_loss = -torch.min(ratio * advantage.detach(), clipped * advantage.detach()).mean()
            value_loss = nn.functional.mse_loss(value.squeeze(-1), reward_tensor)
            entropy_bonus = -0.01 * dist.entropy().mean()

            total_loss = policy_loss + 0.5 * value_loss + entropy_bonus

            if use_kl and expert_probs is not None:
                kl_div = torch.sum(expert_probs * (torch.log(expert_probs + 1e-10) - torch.log(action_probs + 1e-10)))
                total_loss += self.kl_weight * kl_div   # UPDATED: uses annealing kl_weight
                metrics["kl_div"] = kl_div.item()

            metrics.update({
                "policy_loss": policy_loss.item(),
                "value_loss": value_loss.item(),
                "reward": reward,
                "action": action.item(),
                "max_prob": action_probs.max().item(),  # ADDED for convergence visibility
            })

        # Backward + optimize
        self.optimizer.zero_grad()
        total_loss.backward()
        torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=0.5)
        self.optimizer.step()
        self.scheduler.step()

        self._step_count += 1
        metrics.update({"total_loss": total_loss.item(), "step": self._step_count})
        return metrics

    # -------------------------------------------------------------------
    # Chronological training from fixtures
    # -------------------------------------------------------------------

    def train_from_fixtures(
        self,
        phase: int = 1,
        cold: bool = False,
        limit_days: Optional[int] = None,
        resume: bool = False,
        target_season: Union[str, int] = "current",
    ):
        """
        3-Phase Chronological Training with season-aware date selection.

        Phase 1: Imitation Learning (Warm-start from Rule Engine)
        Phase 2: PPO with KL penalty (Fine-tune with constraints)
        Phase 3: Adapter Fine-tuning (League specialization, frozen trunk)

        Args:
            target_season:
                "current"   Per-league season start (default). Joins against
                            leagues.current_season so each league's season
                            start date is respected individually.
                "all"       All available seasons, oldest-first. Full cold retrain.
                int N       Past season by offset: 1 = most recent past season,
                            2 = two seasons ago, etc. Matches enrich_leagues convention.
                str label   Explicit season string, e.g. "2024/2025" or "2025".

        CLI flag: --train-season (see lifecycle.py parse_args)
        """
        from Data.Access.db_helpers import _get_conn

        conn = _get_conn()
        os.makedirs(MODELS_DIR, exist_ok=True)

        print("\n  ============================================================")
        print(f"  RL TRAINING — PHASE {phase} {'(COLD START)' if cold else ''}")
        print("  ============================================================\n")

        # ── Auto-detect active training phase ──────────────────────
        phase_status = check_phase_readiness(conn)
        odds_rows  = phase_status["odds_rows"]
        days_live  = phase_status["days_live"]
        phase2_ready = phase_status["phase2_ready"]
        phase3_ready = phase_status["phase3_ready"]

        if phase3_ready:
            active_phase = 3
            print(f"  [RL] Phase 3 AUTO-ACTIVATED: "
                  f"{odds_rows} odds rows, {days_live} days live.")
        elif phase2_ready:
            active_phase = 2
            print(f"  [RL] Phase 2 AUTO-ACTIVATED: "
                  f"{odds_rows} odds rows, {days_live} days live.")
        else:
            active_phase = 1
            needed_rows = PHASE2_MIN_ODDS_ROWS - odds_rows
            needed_days = max(0, PHASE2_MIN_DAYS_LIVE - days_live)
            print(f"  [RL] Phase 1 active. "
                  f"Phase 2 needs: {needed_rows} more odds rows, "
                  f"{needed_days} more days of live data.")

        self.active_phase = active_phase

        if active_phase == 3 or phase == 3:
            print("  [TRAIN] Freezing Shared Trunk... training Adapters only.")
            for param in self.model.trunk.parameters():
                param.requires_grad = False
        elif cold:
            print("  [TRAIN] Cold start: Starting from base weights (no checkpoint loaded).")

        # ── Season-aware date selection ─────────────────────────────────────────
        # Replaces the old global cutoff (max_seasons_back * 365 days from last date)
        # which forced all leagues to start from the same arbitrary date floor.
        # Now each league's training window begins at its own season start date.
        all_dates, season_label = self._get_season_dates(conn, target_season)

        if not all_dates:
            print(f"  [TRAIN] No fixture dates found for target_season={target_season!r}. "
                  f"Run --enrich-leagues to populate historical data.")
            return

        today_str = datetime.now().strftime("%Y-%m-%d")
        all_dates = [d for d in all_dates if d <= today_str]

        if limit_days:
            all_dates = all_dates[:limit_days]

        print(f"  [TRAIN] Season scope:  {season_label}")
        print(f"  [TRAIN] Window:        {all_dates[0]} → {all_dates[-1]} ({len(all_dates)} fixture-days)")

        # --- Checkpoint setup ---
        CHECKPOINT_DIR = MODELS_DIR / "checkpoints"
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        latest_path = MODELS_DIR / f"phase{phase}_latest.pth"
        total_matches_global = 0
        total_correct_global = 0
        start_day_idx = 0

        # --- Resume from checkpoint ---
        if resume and not cold and latest_path.exists():
            try:
                ckpt = torch.load(latest_path, map_location=self.device, weights_only=False)
                # Architecture mismatch guard
                ckpt_n_actions = ckpt.get("n_actions", 8)
                if ckpt_n_actions != N_ACTIONS:
                    print(f"  [RESUME] ✗ Checkpoint is {ckpt_n_actions}-dim but "
                          f"current model is {N_ACTIONS}-dim. Delete and retrain.")
                    return
                self.model.load_state_dict(ckpt["model_state"], strict=False)
                self.optimizer.load_state_dict(ckpt["optimizer_state"])

                # ── FIX (2026-03-14): Restore scheduler state on resume. ──────────
                # Previously scheduler.state_dict() was not saved, so every --resume
                # re-initialized the scheduler from scratch and the 10x Phase 1 LR
                # reduction below was applied again — compounding the reduction on
                # every resume. Now saved and restored correctly.
                if "scheduler_state" in ckpt:
                    self.scheduler.load_state_dict(ckpt["scheduler_state"])

                start_day_idx = ckpt["day"]
                total_matches_global = ckpt.get("total_matches", 0)
                total_correct_global = ckpt.get("correct_predictions", 0)
                ckpt_season = ckpt.get("target_season", "unknown")
                print(f"  [RESUME] ✓ Loaded checkpoint from Day {start_day_idx}/{len(all_dates)} "
                      f"({ckpt.get('match_date', '?')}) | season={ckpt_season}")
                print(f"  [RESUME]   Matches so far: {total_matches_global} | Correct: {total_correct_global}")
                all_dates = all_dates[start_day_idx:]
                if not all_dates:
                    print(f"  [RESUME] All days already completed. Nothing to do.")
                    return
            except Exception as e:
                print(f"  [RESUME] Failed to load checkpoint: {e} — starting fresh")
                start_day_idx = 0

        # Phase 1 LR reduction: imitation needs 10x lower LR than PPO exploration.
        # Guard with `not resume` so a resumed run does not re-apply the reduction
        # on top of the scheduler state just restored above.
        original_lrs = []
        if active_phase == 1 and not resume:
            for pg in self.optimizer.param_groups:
                original_lrs.append(pg['lr'])
                pg['lr'] = pg['lr'] * 0.1
            print(f"  [TRAIN] Phase 1 LR reduced 10x for stable imitation "
                  f"(base → {self.optimizer.param_groups[0]['lr']:.2e})")

        for day_offset, match_date in enumerate(all_dates):
            day_idx = start_day_idx + day_offset
            day_matches = 0
            day_reward = 0.0
            day_imit_loss = 0.0
            day_kl = 0.0
            day_rl_acc = 0.0
            day_rule_acc = 0.0
            day_grad_norm = 0.0
            day_max_prob = 0.0   # ADDED for convergence visibility

            cursor = conn.execute("""
                SELECT 
                    s.fixture_id, s.league_id, s.home_team_id, 
                    COALESCE(NULLIF(s.home_team_name, ''), t1.name) as h_name,
                    s.away_team_id, 
                    COALESCE(NULLIF(s.away_team_name, ''), t2.name) as a_name,
                    s.home_score, s.away_score,
                    s.season
                FROM schedules s
                LEFT JOIN teams t1 ON s.home_team_id = t1.team_id
                LEFT JOIN teams t2 ON s.away_team_id = t2.team_id
                WHERE s.date = ? AND s.home_score IS NOT NULL AND s.away_score IS NOT NULL
            """, (match_date,))
            fixtures = cursor.fetchall()

            for fix in fixtures:
                fixture_id, league_id, home_tid, h_name, away_tid, a_name, h_score, a_score, season = fix
                outcome = {
                    "result": "home_win" if h_score > a_score else "away_win" if a_score > h_score else "draw",
                    "home_score": h_score, "away_score": a_score
                }

                l_idx = self.registry.get_league_idx(league_id)
                h_idx = self.registry.get_team_idx(home_tid)
                a_idx = self.registry.get_team_idx(away_tid)

                vision_data = self._build_training_vision_data(conn, match_date, league_id, home_tid, h_name, away_tid, a_name, season=season)
                features = FeatureEncoder.encode(vision_data)
                expert_probs = self._get_rule_engine_probs(vision_data)

                if active_phase == 1:
                    metrics = self.train_step(features, l_idx, h_idx, a_idx, expert_probs=expert_probs)
                else:
                    use_kl = (active_phase == 2)
                    metrics = self.train_step(features, l_idx, h_idx, a_idx, outcome=outcome, expert_probs=expert_probs, use_kl=use_kl)

                day_matches += 1
                day_reward += metrics.get("reward", 0.0)
                day_imit_loss += metrics.get("imitation_loss", 0.0)
                day_kl += metrics.get("kl_div", 0.0)
                day_max_prob += metrics.get("max_prob", 0.0)  # ADDED

                # Gradient norm tracking
                total_norm = 0.0
                for p in self.model.parameters():
                    if p.grad is not None:
                        total_norm += p.grad.data.norm(2).item() ** 2
                day_grad_norm += total_norm ** 0.5

                # RL Correctness (vs Actual Outcome — all 30 action types)
                action_idx = metrics.get("action", torch.argmax(
                    self.model.get_action_probs(features, l_idx, h_idx, a_idx)
                ).item())
                correct_actions = self._get_correct_actions(outcome)
                expert_pred_idx = torch.argmax(expert_probs).item()

                if day_idx == 0 and day_matches <= 5:
                    probs_list = expert_probs.squeeze().detach().cpu().tolist()
                    print(f"      [DEBUG] {h_name} vs {a_name}")
                    print(f"        Expert probs: {[round(p, 3) for p in probs_list]}")
                    print(f"        Expert pick: {ACTIONS[expert_pred_idx]['key']} | RL pick: {ACTIONS[action_idx]['key']}")
                    print(f"        Correct actions: {[ACTIONS[a]['key'] for a in correct_actions]}")
                    print(f"        KL: {metrics.get('kl_div', 0.0):.4f} | Imitation loss: {metrics.get('imitation_loss', 0.0):.4f}")

                # Count RL as correct if its action is in the correct set
                if action_idx in correct_actions:
                    day_rl_acc += 1
                # Count Rule Engine as correct if its argmax is in the correct set
                if expert_pred_idx in correct_actions:
                    day_rule_acc += 1

                self.registry.record_match(league_id, home_tid, away_tid)

            if day_matches > 0:
                rl_acc = (day_rl_acc / day_matches) * 100
                rule_acc = (day_rule_acc / day_matches) * 100
                kl = day_kl / day_matches
                gn = day_grad_norm / day_matches
                max_prob_pct = (day_max_prob / day_matches) * 100   # ADDED
                if active_phase == 1:
                    il = day_imit_loss / day_matches
                    print(f"  [Day {day_idx+1:3d}/{start_day_idx + len(all_dates)}] "
                          f"Rule Acc: {rule_acc:4.1f}% | RL Acc: {rl_acc:4.1f}% | "
                          f"KL: {kl:5.3f} | ImitLoss: {il:6.4f} | MaxProb: {max_prob_pct:4.1f}% | "
                          f"GradNorm: {gn:.4f} | Matches: {day_matches}")
                else:
                    rw = day_reward / day_matches
                    print(f"  [Day {day_idx+1:3d}/{start_day_idx + len(all_dates)}] "
                          f"Rule Acc: {rule_acc:4.1f}% | RL Acc: {rl_acc:4.1f}% | "
                          f"KL: {kl:5.3f} | Reward: {rw:6.3f} | MaxProb: {max_prob_pct:4.1f}% | "
                          f"GradNorm: {gn:.4f} | Matches: {day_matches}")

                # --- KL annealing for true outperformance (Phase 2+) ---
                if active_phase >= 2:
                    self.kl_weight = max(0.0, self.kl_weight * 0.995)
                    if self.kl_weight < 0.01:
                        self.kl_weight = 0.0
                    print(f"      [KL annealing] current weight = {self.kl_weight:.4f}")

                # --- Save checkpoint after each day ---
                total_matches_global += day_matches
                total_correct_global += int(day_rl_acc)
                ckpt_data = {
                    "day": day_idx + 1,
                    "total_days": start_day_idx + len(all_dates),
                    "match_date": match_date,
                    "model_state": self.model.state_dict(),
                    "optimizer_state": self.optimizer.state_dict(),
                    # ── FIX (2026-03-14): Persist scheduler state. ──────────────
                    # Without this, every --resume re-initialized the scheduler and
                    # re-applied the 10x Phase 1 LR reduction, compounding it each time.
                    "scheduler_state": self.scheduler.state_dict(),
                    "total_matches": total_matches_global,
                    "correct_predictions": total_correct_global,
                    "phase": active_phase,
                    "n_actions": N_ACTIONS,
                    "odds_rows_at_save": odds_rows,
                    "days_live_at_save": days_live,
                    # Season metadata for auditability and resume awareness
                    "target_season": str(target_season),
                    "season_label": season_label,
                }
                torch.save(ckpt_data, CHECKPOINT_DIR / f"phase{active_phase}_day{day_idx+1:03d}.pth")
                torch.save(ckpt_data, latest_path)

                # Keep only last 5 daily checkpoints
                existing = sorted(CHECKPOINT_DIR.glob(f"phase{active_phase}_day*.pth"))
                while len(existing) > 5:
                    existing[0].unlink()
                    existing = existing[1:]

        # Restore LR after Phase 1
        if original_lrs:
            for pg, lr in zip(self.optimizer.param_groups, original_lrs):
                pg['lr'] = lr

        self.save()
        print(f"\n  [TRAIN] Phase {phase} complete. Model saved.")

    def _build_training_vision_data(
        self, conn, match_date: str, league_id: str,
        home_team_id: str, home_team_name: str,
        away_team_id: str, away_team_name: str,
        season: str = None,
    ) -> Dict[str, Any]:
        """
        Build a vision_data dict from historical fixtures for training.
        Uses ONLY data before match_date (no future leakage).
        """
        from Data.Access.league_db import computed_standings

        # Get last 10 home team matches before this date
        home_form = self._get_team_form(conn, home_team_id, home_team_name, match_date)
        away_form = self._get_team_form(conn, away_team_id, away_team_name, match_date)
        h2h = self._get_h2h(conn, home_team_id, away_team_id, match_date)

        # P0 Fix 3: Reconstruct historical standings as of match_date
        standings = []
        if league_id:
            try:
                standings = computed_standings(
                    conn=conn, league_id=league_id,
                    season=season, before_date=match_date
                )
            except Exception:
                standings = []

        return {
            "h2h_data": {
                "home_team": home_team_name,
                "away_team": away_team_name,
                "home_last_10_matches": home_form,
                "away_last_10_matches": away_form,
                "head_to_head": h2h,
                "region_league": league_id,
            },
            "standings": standings,
        }

    def _get_team_form(self, conn, team_id: str, team_name: str,
                       before_date: str) -> List[Dict]:
        """Get last 10 matches for a team before a given date."""
        cursor = conn.execute("""
            SELECT date, home_team_name, away_team_name, home_score, away_score
            FROM schedules
            WHERE (home_team_id = ? OR away_team_id = ?)
              AND date < ?
              AND home_score IS NOT NULL AND away_score IS NOT NULL
              AND home_score != '' AND away_score != ''
              AND (match_status = 'finished' OR match_status IS NULL)
            ORDER BY date DESC
            LIMIT 10
        """, (team_id, team_id, before_date))

        matches = []
        for row in cursor.fetchall():
            hs, as_ = int(row[3] or 0), int(row[4] or 0)
            winner = "Home" if hs > as_ else "Away" if as_ > hs else "Draw"
            matches.append({
                "date": row[0],
                "home": row[1],
                "away": row[2],
                "score": f"{hs}-{as_}",
                "winner": winner,
            })
        return matches

    def _get_h2h(self, conn, home_id: str, away_id: str,
                 before_date: str) -> List[Dict]:
        """Get H2H matches between two teams before a given date (540-day window)."""
        # P0 Fix 2: Apply 18-month (540-day) cutoff matching live Rule Engine
        cutoff_date = (datetime.strptime(before_date, "%Y-%m-%d")
                       - timedelta(days=540)).strftime("%Y-%m-%d")

        cursor = conn.execute("""
            SELECT date, home_team_name, away_team_name, home_score, away_score
            FROM schedules
            WHERE ((home_team_id = ? AND away_team_id = ?)
                OR (home_team_id = ? AND away_team_id = ?))
              AND date < ?
              AND date >= ?
              AND home_score IS NOT NULL AND away_score IS NOT NULL
              AND home_score != '' AND away_score != ''
              AND (match_status = 'finished' OR match_status IS NULL)
            ORDER BY date DESC
            LIMIT 10
        """, (home_id, away_id, away_id, home_id, before_date, cutoff_date))

        matches = []
        for row in cursor.fetchall():
            hs, as_ = int(row[3] or 0), int(row[4] or 0)
            winner = "Home" if hs > as_ else "Away" if as_ > hs else "Draw"
            matches.append({
                "date": row[0],
                "home": row[1],
                "away": row[2],
                "score": f"{hs}-{as_}",
                "winner": winner,
            })
        return matches

    # -------------------------------------------------------------------
    # Online update (from new prediction outcomes)
    # -------------------------------------------------------------------

    def update_from_outcomes(self, reviewed_predictions: List[Dict[str, Any]]):
        """
        Online learning from new prediction outcomes.
        Called after outcome_reviewer completes a batch.
        """
        if not reviewed_predictions:
            return

        self.load()  # Load latest model
        updated = 0

        for pred in reviewed_predictions:
            if pred.get("outcome_correct") not in ("True", "False", "1", "0"):
                continue

            is_correct = pred.get("outcome_correct") in ("True", "1")

            # Build vision_data from prediction record
            vision_data = {
                "h2h_data": {
                    "home_team": pred.get("home_team", ""),
                    "away_team": pred.get("away_team", ""),
                    "home_last_10_matches": [],
                    "away_last_10_matches": [],
                    "head_to_head": [],
                    "region_league": pred.get("region_league", "GLOBAL"),
                },
                "standings": [],
            }

            features = FeatureEncoder.encode(vision_data)

            league_id = pred.get("region_league", "GLOBAL")
            home_tid = pred.get("home_team_id", "GLOBAL")
            away_tid = pred.get("away_team_id", "GLOBAL")

            l_idx = self.registry.get_league_idx(league_id)
            h_idx = self.registry.get_team_idx(home_tid)
            a_idx = self.registry.get_team_idx(away_tid)

            # Simple reward from correctness
            outcome = {
                "result": "home_win" if is_correct else "draw",  # Simplified
                "home_score": int(pred.get("home_score", 0) or 0),
                "away_score": int(pred.get("away_score", 0) or 0),
            }

            self.train_step(features, l_idx, h_idx, a_idx, outcome)
            updated += 1

        if updated > 0:
            self.save()
            print(f"  [RL] Updated model from {updated} new outcomes")

    # -------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------

    def save(self):
        """Save model and registry."""
        os.makedirs(MODELS_DIR, exist_ok=True)
        torch.save(self.model.state_dict(), BASE_MODEL_PATH)
        self.registry.save()

    def load(self):
        """Load model and registry if they exist."""
        if BASE_MODEL_PATH.exists():
            try:
                state_dict = torch.load(BASE_MODEL_PATH, map_location=self.device, weights_only=True)
                self.model.load_state_dict(state_dict, strict=False)
            except Exception as e:
                print(f"  [RL] Could not load model: {e}")

        self.registry = AdapterRegistry()  # Reloads from disk