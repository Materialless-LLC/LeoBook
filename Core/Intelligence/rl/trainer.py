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
- Max 2-season lookback window
- Last-10 matches prioritized via recency weighting
- Prediction accuracy is the primary reward signal
"""

import os
import torch
import torch.nn as nn
import torch.optim as optim
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

from .model import LeoBookRLModel
from .feature_encoder import FeatureEncoder
from .adapter_registry import AdapterRegistry

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
        1. Build features using ONLY data before D (max 2 seasons back)
        2. For each fixture on day D:
            a. Encode features
            b. Model predicts action (market + stake)
            c. After outcome: compute composite reward
            d. PPO gradient update
    """

    def __init__(
        self,
        lr_base: float = 3e-4,
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

    # -------------------------------------------------------------------
    # Composite Reward (prediction accuracy is primary)
    # -------------------------------------------------------------------

    @staticmethod
    def compute_reward(
        predicted_action: int,
        actual_outcome: Dict[str, Any],
        pred_probs: Optional[torch.Tensor] = None,
    ) -> float:
        """
        Composite reward with prediction accuracy as the backbone.

        Args:
            predicted_action: Index into ACTION_NAMES
            actual_outcome: Dict with keys:
                - result: "home_win" | "draw" | "away_win"
                - home_score, away_score: int
                - odds: optional dict of market odds
            pred_probs: Optional softmax probabilities for calibration

        Returns:
            Composite reward scalar.
        """
        result = actual_outcome.get("result", "")
        home_score = actual_outcome.get("home_score", 0)
        away_score = actual_outcome.get("away_score", 0)
        total_goals = home_score + away_score

        # --- 1. Prediction Accuracy (weight: 1.0) ---
        # Map outcome to correct actions
        correct_actions = set()
        if result == "home_win":
            correct_actions = {0}  # home_win
        elif result == "draw":
            correct_actions = {1}  # draw
        elif result == "away_win":
            correct_actions = {2}  # away_win

        # Over/Under 2.5
        if total_goals > 2:
            correct_actions.add(3)  # over_2.5
        else:
            correct_actions.add(4)  # under_2.5

        # BTTS
        if home_score > 0 and away_score > 0:
            correct_actions.add(5)  # btts_yes
        else:
            correct_actions.add(6)  # btts_no

        prediction_correct = 1.0 if predicted_action in correct_actions else -0.5

        # --- 2. Calibration (weight: 0.6) ---
        calibration_score = 0.0
        if pred_probs is not None:
            # Brier score component for the 1X2 outcome
            actual_vec = torch.zeros(3)
            if result == "home_win":
                actual_vec[0] = 1.0
            elif result == "draw":
                actual_vec[1] = 1.0
            elif result == "away_win":
                actual_vec[2] = 1.0

            brier = ((pred_probs[:3] - actual_vec) ** 2).sum().item()
            calibration_score = 1.0 - brier  # Higher is better

        # --- 3. ROI component (weight: 0.4) ---
        # Simplified: correct prediction with good odds = bonus
        odds = actual_outcome.get("odds", {})
        roi_score = 0.0
        if predicted_action in correct_actions and predicted_action < 3:
            action_name = LeoBookRLModel.ACTION_NAMES[predicted_action]
            odd_val = odds.get(action_name, 2.0)
            roi_score = (odd_val - 1.0) / odd_val  # Profit margin

        # --- 4. Abstention bonus/penalty ---
        if predicted_action == 7:  # no_bet
            # Small positive reward for correctly abstaining on uncertain matches
            if pred_probs is not None and pred_probs[:3].max().item() < 0.4:
                prediction_correct = 0.3  # Reward uncertainty awareness
            else:
                prediction_correct = -0.1  # Penalty for unnecessary abstention

        # --- Composite ---
        reward = (
            1.0 * prediction_correct
            + 0.6 * calibration_score
            + 0.4 * roi_score
        )

        return reward

    # -------------------------------------------------------------------
    # Training step (PPO)
    # -------------------------------------------------------------------

    def train_step(
        self,
        features: torch.Tensor,
        league_idx: int,
        home_team_idx: int,
        away_team_idx: int,
        outcome: Dict[str, Any],
    ) -> Dict[str, float]:
        """
        Single PPO training step for one match.

        Returns dict with loss components for logging.
        """
        self.model.train()
        features = features.to(self.device)

        # Forward pass
        policy_logits, value, stake = self.model(
            features, league_idx, home_team_idx, away_team_idx
        )

        # Action selection (during training: sample from policy)
        action_probs = torch.softmax(policy_logits, dim=-1)
        dist = torch.distributions.Categorical(action_probs)
        action = dist.sample()
        log_prob = dist.log_prob(action)

        # Compute reward
        reward = self.compute_reward(
            action.item(), outcome, action_probs.detach().squeeze()
        )
        reward_tensor = torch.tensor([reward], dtype=torch.float32, device=self.device)

        # Advantage = reward - value (simple 1-step)
        advantage = reward_tensor - value.squeeze(-1)

        # PPO clipped loss
        ratio = torch.exp(log_prob - log_prob.detach())  # Will be 1.0 for fresh samples
        clipped = torch.clamp(ratio, 1.0 - self.clip_epsilon, 1.0 + self.clip_epsilon)
        policy_loss = -torch.min(ratio * advantage.detach(), clipped * advantage.detach())

        # Value loss
        value_loss = torch.nn.functional.mse_loss(value.squeeze(-1), reward_tensor)

        # Entropy bonus (encourage exploration)
        entropy = dist.entropy()
        entropy_bonus = -0.01 * entropy

        # Total loss
        total_loss = policy_loss + 0.5 * value_loss + entropy_bonus

        # Backward + optimize
        self.optimizer.zero_grad()
        total_loss.backward()
        torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=0.5)
        self.optimizer.step()
        self.scheduler.step()

        self._step_count += 1

        return {
            "total_loss": total_loss.item(),
            "policy_loss": policy_loss.item(),
            "value_loss": value_loss.item(),
            "entropy": entropy.item(),
            "reward": reward,
            "action": action.item(),
            "step": self._step_count,
        }

    # -------------------------------------------------------------------
    # Chronological training from fixtures
    # -------------------------------------------------------------------

    def train_from_fixtures(self, limit_days: Optional[int] = None):
        """
        Train chronologically from historical fixtures.
        Iterates day-by-day, using only data available before each day.

        Args:
            limit_days: Optional limit on number of training days (for testing).
        """
        from Data.Access.db_helpers import _get_conn
        import os

        conn = _get_conn()
        os.makedirs(MODELS_DIR, exist_ok=True)

        print("\n  ============================================================")
        print("  RL TRAINING — Chronological Walk-Through")
        print("  ============================================================\n")

        # Get all fixture dates in chronological order
        cursor = conn.execute("""
            SELECT DISTINCT date FROM schedules
            WHERE date IS NOT NULL
              AND home_score IS NOT NULL
              AND away_score IS NOT NULL
            ORDER BY date ASC
        """)
        all_dates = [row[0] for row in cursor.fetchall()]

        if not all_dates:
            print("  [TRAIN] No fixtures with results found.")
            return

        # Apply 2-season window: only use recent data
        # Also exclude future dates (unplayed fixtures produce garbage rewards)
        today_str = datetime.now().strftime("%Y-%m-%d")
        try:
            latest_date = datetime.strptime(all_dates[-1], "%Y-%m-%d")
            cutoff = latest_date - timedelta(days=self.max_seasons_back * 365)
            cutoff_str = cutoff.strftime("%Y-%m-%d")
            all_dates = [d for d in all_dates if d >= cutoff_str and d <= today_str]
        except (ValueError, IndexError):
            pass

        if limit_days:
            all_dates = all_dates[:limit_days]

        print(f"  [TRAIN] Training on {len(all_dates)} match days")
        print(f"  [TRAIN] Date range: {all_dates[0]} → {all_dates[-1]}")

        total_matches = 0
        total_reward = 0.0
        correct_predictions = 0
        log_interval = max(1, len(all_dates) // 20)  # Log ~20 times

        for day_idx, match_date in enumerate(all_dates):
            # Get all fixtures on this date
            cursor = conn.execute("""
                SELECT fixture_id, league_id, home_team_id, home_team_name,
                       away_team_id, away_team_name, home_score, away_score,
                       season
                FROM schedules
                WHERE date = ? AND home_score IS NOT NULL AND away_score IS NOT NULL
            """, (match_date,))
            day_fixtures = cursor.fetchall()

            for fix in day_fixtures:
                fix_id = fix[0]
                league_id = fix[1] or "GLOBAL"
                home_team_id = fix[2] or "GLOBAL"
                away_team_id = fix[4] or "GLOBAL"
                home_score = fix[6]
                away_score = fix[7]

                # Determine outcome
                if home_score > away_score:
                    result = "home_win"
                elif home_score < away_score:
                    result = "away_win"
                else:
                    result = "draw"

                outcome = {
                    "result": result,
                    "home_score": home_score,
                    "away_score": away_score,
                }

                # Get adapter indices (auto-registers cold-start entities)
                l_idx = self.registry.get_league_idx(league_id)
                h_idx = self.registry.get_team_idx(home_team_id)
                a_idx = self.registry.get_team_idx(away_team_id)

                # Build minimal features from fixture data
                # (In production, full vision_data is used; here we use what's available)
                vision_data = self._build_training_vision_data(
                    conn, match_date, league_id,
                    home_team_id, fix[3], away_team_id, fix[5]
                )

                features = FeatureEncoder.encode(vision_data)

                # Train step
                metrics = self.train_step(features, l_idx, h_idx, a_idx, outcome)

                total_matches += 1
                total_reward += metrics["reward"]

                # Track prediction accuracy
                action_name = LeoBookRLModel.ACTION_NAMES[metrics["action"]]
                if action_name == result:
                    correct_predictions += 1

                # Record match for fine-tune threshold tracking
                self.registry.record_match(league_id, home_team_id, away_team_id)

            # Periodic logging
            if day_idx > 0 and day_idx % log_interval == 0:
                acc = correct_predictions / max(total_matches, 1) * 100
                avg_r = total_reward / max(total_matches, 1)
                print(f"  [TRAIN] Day {day_idx}/{len(all_dates)} | "
                      f"Matches: {total_matches} | "
                      f"Accuracy: {acc:.1f}% | "
                      f"Avg Reward: {avg_r:.3f}")

        # Final stats
        acc = correct_predictions / max(total_matches, 1) * 100
        avg_r = total_reward / max(total_matches, 1)
        print(f"\n  [TRAIN] COMPLETE — {total_matches} matches, "
              f"{acc:.1f}% accuracy, {avg_r:.3f} avg reward")

        # Save
        self.save()
        print(f"  [TRAIN] Model saved to {MODELS_DIR}")

    def _build_training_vision_data(
        self, conn, match_date: str, league_id: str,
        home_team_id: str, home_team_name: str,
        away_team_id: str, away_team_name: str,
    ) -> Dict[str, Any]:
        """
        Build a vision_data dict from historical fixtures for training.
        Uses ONLY data before match_date (no future leakage).
        """
        # Get last 10 home team matches before this date
        home_form = self._get_team_form(conn, home_team_id, home_team_name, match_date)
        away_form = self._get_team_form(conn, away_team_id, away_team_name, match_date)
        h2h = self._get_h2h(conn, home_team_id, away_team_id, match_date)

        return {
            "h2h_data": {
                "home_team": home_team_name,
                "away_team": away_team_name,
                "home_last_10_matches": home_form,
                "away_last_10_matches": away_form,
                "head_to_head": h2h,
                "region_league": league_id,
            },
            "standings": [],  # Historical standings not easily reconstructable
        }

    def _get_team_form(self, conn, team_id: str, team_name: str,
                       before_date: str) -> List[Dict]:
        """Get last 10 matches for a team before a given date."""
        cursor = conn.execute("""
            SELECT date, home_team_name, away_team_name, home_score, away_score
            FROM schedules
            WHERE (home_team_id = ? OR away_team_id = ?)
              AND date < ?
              AND home_score IS NOT NULL
            ORDER BY date DESC
            LIMIT 10
        """, (team_id, team_id, before_date))

        matches = []
        for row in cursor.fetchall():
            matches.append({
                "date": row[0],
                "home": row[1],
                "away": row[2],
                "score": f"{row[3]}-{row[4]}",
            })
        return matches

    def _get_h2h(self, conn, home_id: str, away_id: str,
                 before_date: str) -> List[Dict]:
        """Get H2H matches between two teams before a given date."""
        cursor = conn.execute("""
            SELECT date, home_team_name, away_team_name, home_score, away_score
            FROM schedules
            WHERE ((home_team_id = ? AND away_team_id = ?)
                OR (home_team_id = ? AND away_team_id = ?))
              AND date < ?
              AND home_score IS NOT NULL
            ORDER BY date DESC
            LIMIT 10
        """, (home_id, away_id, away_id, home_id, before_date))

        matches = []
        for row in cursor.fetchall():
            matches.append({
                "date": row[0],
                "home": row[1],
                "away": row[2],
                "score": f"{row[3]}-{row[4]}",
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
