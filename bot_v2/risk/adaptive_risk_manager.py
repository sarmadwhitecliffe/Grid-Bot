"""Adaptive Risk Management System - 5-tier performance-based position sizing"""

import json
import logging
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Global settings loaded from adaptive risk tiers config
SETTINGS: Dict[str, Any] = {
    "lookback_trades": 30,
    "boundary_policy": "min_inclusive_max_exclusive",
    "tier_eval_order": None,  # list of tier names, highest to lowest
    "pf_min_trades_for_validation": None,
    "enable_kill_switch": True,
    "kill_switch_consecutive_losses": 7,
    "kill_switch_drawdown_pct": 30.0,
    "kill_switch_pf_min_trades": 30,  # Minimum trades before PF kill switch activates
    "enable_drawdown_scaling": True,
    "drawdown_scale_threshold_pct": 10.0,
    "drawdown_scale_factor": 0.5,
    "enable_portfolio_heat_limit": True,
    "max_portfolio_heat_pct": 8.0,  # as percent of total capital
    "tier_portfolio_caps": {},
    "drawdown_scaling_overrides": {},
    "promotion_rules": {},
    "demotion_rules": {},
}


@dataclass
class RiskTier:
    name: str
    min_trades: int
    max_trades: Optional[int]
    profit_factor_min: float
    profit_factor_max: Optional[float]
    sharpe_ratio_min: float
    win_rate_min: Optional[float]
    max_drawdown_max: Optional[float]
    consecutive_losses_max: Optional[int]
    capital_allocation: float
    min_leverage: int  # Minimum leverage for tier (floor for Kelly)
    max_leverage: int  # Maximum leverage for tier (ceiling for Kelly)
    max_position_size_usd: Optional[int]
    description: str


def _load_settings_from_config(config: Dict[str, Any]) -> None:
    """Populate global SETTINGS from config dict (best-effort with defaults)."""
    try:
        settings = config.get("settings", {}) or {}
        # Map known fields with fallbacks
        SETTINGS.update(
            {
                "lookback_trades": settings.get(
                    "lookback_trades", SETTINGS["lookback_trades"]
                ),
                "boundary_policy": settings.get(
                    "boundary_policy", SETTINGS["boundary_policy"]
                ),
                "tier_eval_order": settings.get(
                    "tier_eval_order", SETTINGS["tier_eval_order"]
                ),
                "pf_min_trades_for_validation": settings.get(
                    "pf_min_trades_for_validation",
                    SETTINGS["pf_min_trades_for_validation"],
                ),
                "enable_kill_switch": settings.get(
                    "enable_kill_switch", SETTINGS["enable_kill_switch"]
                ),
                "kill_switch_consecutive_losses": settings.get(
                    "kill_switch_consecutive_losses",
                    SETTINGS["kill_switch_consecutive_losses"],
                ),
                "kill_switch_drawdown_pct": settings.get(
                    "kill_switch_drawdown_pct", SETTINGS["kill_switch_drawdown_pct"]
                ),
                "kill_switch_pf_min_trades": settings.get(
                    "kill_switch_pf_min_trades", SETTINGS["kill_switch_pf_min_trades"]
                ),
                "enable_drawdown_scaling": settings.get(
                    "enable_drawdown_scaling", SETTINGS["enable_drawdown_scaling"]
                ),
                "drawdown_scale_threshold_pct": settings.get(
                    "drawdown_scale_threshold_pct",
                    SETTINGS["drawdown_scale_threshold_pct"],
                ),
                "drawdown_scale_factor": settings.get(
                    "drawdown_scale_factor", SETTINGS["drawdown_scale_factor"]
                ),
                "enable_portfolio_heat_limit": settings.get(
                    "enable_portfolio_heat_limit",
                    SETTINGS["enable_portfolio_heat_limit"],
                ),
                "max_portfolio_heat_pct": settings.get(
                    "max_portfolio_heat_pct", SETTINGS["max_portfolio_heat_pct"]
                ),
                "tier_portfolio_caps": settings.get(
                    "tier_portfolio_caps", SETTINGS["tier_portfolio_caps"]
                )
                or {},
                "drawdown_scaling_overrides": settings.get(
                    "drawdown_scaling_overrides", SETTINGS["drawdown_scaling_overrides"]
                )
                or {},
                "promotion_rules": settings.get(
                    "promotion_rules", SETTINGS["promotion_rules"]
                )
                or {},
                "demotion_rules": settings.get(
                    "demotion_rules", SETTINGS["demotion_rules"]
                )
                or {},
            }
        )
    except Exception:
        logger.warning(
            "Failed to parse adaptive risk settings; using defaults", exc_info=True
        )


def load_risk_tiers_from_config(
    config_path: str = "config/adaptive_risk_tiers.json",
) -> List[RiskTier]:
    """
    Load risk tiers from JSON configuration file with validation.

    Validates:
    - All required fields present
    - Parameter ranges valid (leverage > 0, percentages in 0-100, etc.)
    - Tier ordering makes sense
    - No overlapping criteria
    """
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
        # Load settings side-by-side (global for this module)
        _load_settings_from_config(config)

        tiers = []
        for idx, tier_config in enumerate(config["tiers"]):
            # Validate required fields
            required_fields = [
                "name",
                "min_trades",
                "min_profit_factor",
                "capital_allocation_pct",
                "max_leverage",
            ]
            missing = [f for f in required_fields if f not in tier_config]
            if missing:
                raise ValueError(f"Tier {idx} missing required fields: {missing}")

            # Validate parameter ranges
            if tier_config["max_leverage"] <= 0:
                raise ValueError(
                    f"Tier {tier_config['name']}: max_leverage must be > 0, got {tier_config['max_leverage']}"
                )

            if not (0 <= tier_config["capital_allocation_pct"] <= 100):
                raise ValueError(
                    f"Tier {tier_config['name']}: capital_allocation_pct must be 0-100, "
                    f"got {tier_config['capital_allocation_pct']}"
                )

            if tier_config["min_trades"] < 0:
                raise ValueError(
                    f"Tier {tier_config['name']}: min_trades must be >= 0, got {tier_config['min_trades']}"
                )

            if tier_config["min_profit_factor"] < 0:
                raise ValueError(
                    f"Tier {tier_config['name']}: min_profit_factor must be >= 0, "
                    f"got {tier_config['min_profit_factor']}"
                )

            # Validate optional fields if present
            if (
                "max_position_size_usd" in tier_config
                and tier_config["max_position_size_usd"] is not None
            ):
                if tier_config["max_position_size_usd"] <= 0:
                    raise ValueError(
                        f"Tier {tier_config['name']}: max_position_size_usd must be > 0 if specified, "
                        f"got {tier_config['max_position_size_usd']}"
                    )

            # Coerce nullable fields to safe defaults
            min_sharpe_raw = tier_config.get("min_sharpe_ratio", -999)
            if min_sharpe_raw is None:
                min_sharpe_raw = -999

            tier = RiskTier(
                name=tier_config["name"],
                min_trades=tier_config["min_trades"],
                max_trades=tier_config.get("max_trades"),
                profit_factor_min=tier_config["min_profit_factor"],
                profit_factor_max=tier_config.get("max_profit_factor"),
                sharpe_ratio_min=min_sharpe_raw,
                win_rate_min=tier_config.get("min_win_rate"),
                max_drawdown_max=tier_config.get("max_drawdown"),
                consecutive_losses_max=tier_config.get("max_consecutive_losses"),
                capital_allocation=tier_config["capital_allocation_pct"] / 100.0,
                min_leverage=tier_config.get(
                    "min_leverage", 1
                ),  # Default to 1 if not specified
                max_leverage=tier_config["max_leverage"],
                max_position_size_usd=tier_config.get("max_position_size_usd"),
                description=tier_config["description"],
            )
            tiers.append(tier)

        # Sort tiers by either configured evaluation order or capital allocation
        tiers_by_name = {t.name: t for t in tiers}
        eval_order = SETTINGS.get("tier_eval_order")
        if isinstance(eval_order, list) and all(isinstance(n, str) for n in eval_order):
            tiers_sorted: List[RiskTier] = []
            for name in eval_order:
                if name in tiers_by_name:
                    tiers_sorted.append(tiers_by_name[name])
            # Append any tiers not listed explicitly, ordered by capital allocation
            remaining = [
                t for t in tiers if t.name not in {ti.name for ti in tiers_sorted}
            ]
            tiers_sorted.extend(
                sorted(remaining, key=lambda t: t.capital_allocation, reverse=True)
            )
        else:
            tiers_sorted = sorted(
                tiers, key=lambda t: t.capital_allocation, reverse=True
            )

        # Validate tier ordering makes sense
        for i in range(len(tiers_sorted) - 1):
            current = tiers_sorted[i]
            next_tier = tiers_sorted[i + 1]

            # Higher tiers should have higher requirements
            if current.profit_factor_min < next_tier.profit_factor_min:
                logger.warning(
                    f"Tier ordering: {current.name} (higher allocation) has lower PF requirement "
                    f"({current.profit_factor_min}) than {next_tier.name} ({next_tier.profit_factor_min})"
                )

        logger.info(
            f"✅ Loaded and validated {len(tiers)} risk tiers from {config_path}"
        )
        for tier in tiers_sorted:
            logger.info(
                f"  {tier.name}: {tier.capital_allocation * 100:.0f}% capital @ {tier.min_leverage}-{tier.max_leverage}x leverage "
                f"({tier.min_trades}+ trades, PF>{tier.profit_factor_min:.1f})"
            )

        return tiers_sorted

    except FileNotFoundError:
        logger.warning(f"Config file not found: {config_path}, using default tiers")
        return get_default_tiers()
    except (ValueError, KeyError) as e:
        logger.error(
            f"⚠️  Invalid tier configuration in {config_path}: {e}. Using default tiers"
        )
        return get_default_tiers()
    except Exception as e:
        logger.error(
            f"Error loading risk tiers from config: {e}, using default tiers",
            exc_info=True,
        )
        return get_default_tiers()


def get_default_tiers() -> List[RiskTier]:
    """Get default risk tiers (fallback if config file not available)."""
    PROBATION = RiskTier(
        "PROBATION",
        0,
        14,
        0.0,
        None,
        -999,
        None,
        None,
        None,
        0.10,
        1,
        2,
        500,
        "Learning phase",
    )
    CONSERVATIVE = RiskTier(
        "CONSERVATIVE",
        15,
        None,
        0.8,
        1.19,
        -0.5,
        0.35,
        None,
        None,
        0.20,
        1,
        3,
        1000,
        "Underperforming",
    )
    STANDARD = RiskTier(
        "STANDARD",
        15,
        None,
        1.2,
        1.49,
        0.5,
        0.40,
        0.25,
        None,
        0.40,
        1,
        5,
        3000,
        "Solid performance",
    )
    AGGRESSIVE = RiskTier(
        "AGGRESSIVE",
        30,
        None,
        1.5,
        1.99,
        1.0,
        0.45,
        0.15,
        5,
        0.60,
        1,
        7,
        5000,
        "Proven performer",
    )
    CHAMPION = RiskTier(
        "CHAMPION",
        50,
        None,
        2.0,
        None,
        1.5,
        0.50,
        0.12,
        3,
        0.80,
        1,
        8,
        10000,
        "Elite performance",
    )
    return [CHAMPION, AGGRESSIVE, STANDARD, CONSERVATIVE, PROBATION]


# Load tiers from config file on module import
ALL_TIERS = load_risk_tiers_from_config()


@dataclass
class PerformanceMetrics:
    symbol: str
    total_trades: int
    lookback_trades: int
    profit_factor: float
    sharpe_ratio: float
    win_rate: float
    max_drawdown: float
    avg_win: float
    avg_loss: float
    avg_win_r: float
    avg_r_multiple: float
    expectancy_r: float
    max_consecutive_losses: int
    current_consecutive_losses: int
    std_dev_returns: float
    current_equity: float
    peak_equity: float
    last_calculated: str
    first_trade_date: Optional[str]
    current_drawdown_pct: float
    recovery_factor: float

    def is_valid_sample(self) -> bool:
        return self.total_trades >= 15


class PerformanceAnalyzer:
    @staticmethod
    def calculate_metrics(
        symbol: str,
        trade_history: List[Dict[str, Any]],
        lookback_trades: int = 20,
        initial_capital: float = 100.0,
    ) -> PerformanceMetrics:
        symbol_trades = [t for t in trade_history if t.get("symbol") == symbol]
        total_trades = len(symbol_trades)

        if total_trades == 0:
            return PerformanceMetrics(
                symbol,
                0,
                0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0,
                0,
                0.0,
                initial_capital,
                initial_capital,
                datetime.now(timezone.utc).isoformat(),
                None,
                0.0,
                0.0,
            )

        recent_trades = (
            symbol_trades[-lookback_trades:]
            if total_trades > lookback_trades
            else symbol_trades
        )
        # Support both 'pnl' and 'pnl_usd' field names
        wins = [
            t for t in recent_trades if float(t.get("pnl_usd") or t.get("pnl", 0)) > 0
        ]
        losses = [
            t for t in recent_trades if float(t.get("pnl_usd") or t.get("pnl", 0)) < 0
        ]

        total_wins = len(wins)
        total_losses = len(losses)
        win_rate = total_wins / len(recent_trades) if recent_trades else 0.0

        # Support both 'pnl' and 'pnl_usd' field names
        gross_profit = sum(float(t.get("pnl_usd") or t.get("pnl", 0)) for t in wins)
        gross_loss = abs(
            sum(float(t.get("pnl_usd") or t.get("pnl", 0)) for t in losses)
        )
        profit_factor = (
            gross_profit / gross_loss
            if gross_loss > 0
            else (10.0 if gross_profit > 0 else 0.0)
        )

        avg_win = gross_profit / total_wins if total_wins > 0 else 0.0
        avg_loss = gross_loss / total_losses if total_losses > 0 else 0.0

        # Support both 'r_multiple' and 'realized_r_multiple' field names
        r_multiples = [
            float(t.get("realized_r_multiple") or t.get("r_multiple", 0))
            for t in recent_trades
        ]
        win_r_multiples = [
            float(t.get("realized_r_multiple") or t.get("r_multiple", 0)) for t in wins
        ]
        avg_win_r = (
            sum(win_r_multiples) / len(win_r_multiples) if win_r_multiples else 0.0
        )
        avg_r_multiple = sum(r_multiples) / len(r_multiples) if r_multiples else 0.0
        expectancy_r = (
            win_rate * avg_win - (1 - win_rate) * avg_loss if recent_trades else 0.0
        )

        if len(r_multiples) > 1:
            mean_return = sum(r_multiples) / len(r_multiples)
            variance = sum((r - mean_return) ** 2 for r in r_multiples) / len(
                r_multiples
            )
            std_dev = math.sqrt(variance) if variance > 0 else 0.0001
            sharpe_ratio = (
                (mean_return / std_dev) * math.sqrt(252) if std_dev > 0 else 0.0
            )
            std_dev_returns = std_dev
        else:
            sharpe_ratio = 0.0
            std_dev_returns = 0.0

        equity = initial_capital
        peak_equity = equity
        max_dd = 0.0

        for trade in symbol_trades:
            # Support both 'pnl' and 'pnl_usd' field names
            pnl = float(trade.get("pnl_usd") or trade.get("pnl", 0))
            equity += pnl
            if equity > peak_equity:
                peak_equity = equity
            dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
            max_dd = max(max_dd, dd)

        current_equity = equity
        current_dd_pct = (
            (peak_equity - current_equity) / peak_equity if peak_equity > 0 else 0.0
        )
        starting_equity = initial_capital
        net_profit = current_equity - starting_equity
        recovery_factor = net_profit / (max_dd * starting_equity) if max_dd > 0 else 0.0

        max_consec_losses = 0
        current_consec_losses = 0
        for trade in reversed(recent_trades):
            # Support both 'pnl' and 'pnl_usd' field names
            if float(trade.get("pnl_usd") or trade.get("pnl", 0)) < 0:
                current_consec_losses += 1
            else:
                break

        consec_count = 0
        for trade in recent_trades:
            # Support both 'pnl' and 'pnl_usd' field names
            if float(trade.get("pnl_usd") or trade.get("pnl", 0)) < 0:
                consec_count += 1
                max_consec_losses = max(max_consec_losses, consec_count)
            else:
                consec_count = 0

        # Support both 'timestamp' and 'exit_time' field names
        first_trade_date = (
            symbol_trades[0].get("timestamp") or symbol_trades[0].get("exit_time")
            if symbol_trades
            else None
        )

        return PerformanceMetrics(
            symbol,
            total_trades,
            len(recent_trades),
            profit_factor,
            sharpe_ratio,
            win_rate,
            max_dd,
            avg_win,
            avg_loss,
            avg_win_r,
            avg_r_multiple,
            expectancy_r,
            max_consec_losses,
            current_consec_losses,
            std_dev_returns,
            current_equity,
            peak_equity,
            datetime.now(timezone.utc).isoformat(),
            first_trade_date,
            current_dd_pct,
            recovery_factor,
        )


class RiskTierClassifier:
    @staticmethod
    def classify(
        metrics: PerformanceMetrics, tier_history: Optional[Dict[str, Any]] = None
    ) -> RiskTier:
        # Get PROBATION tier from ALL_TIERS (it's the last one)
        probation_tier = ALL_TIERS[-1]

        # Get current tier info from history for hysteresis
        current_tier_name = tier_history.get("current_tier") if tier_history else None
        current_tier_obj = (
            next((t for t in ALL_TIERS if t.name == current_tier_name), None)
            if current_tier_name
            else None
        )
        trades_in_tier = tier_history.get("trades_in_tier", 0) if tier_history else 0
        tier_history.get("tier_entry_time") if tier_history else None
        consecutive_losses_in_tier = (
            tier_history.get("consecutive_losses_in_tier", 0) if tier_history else 0
        )

        # Load promotion/demotion rules from settings
        promotion_rules = SETTINGS.get("promotion_rules", {}) or {}
        demotion_rules = SETTINGS.get("demotion_rules", {}) or {}

        promote_after_trades = promotion_rules.get("promote_after_trades", 0)
        promote_buffer_pf = promotion_rules.get("promote_buffer_pf", 0.0)
        min_stay_trades = promotion_rules.get("min_stay_trades", 0)

        demote_after_losses = demotion_rules.get("demote_after_losses", 0)
        demote_buffer_pf = demotion_rules.get("demote_buffer_pf", 0.0)

        # Classify strictly by configured criteria. Do not hard-code a 15-trade minimum.
        # Iterate from highest to lowest tier and select the first tier whose MINIMUM requirements are met.
        best_eligible_tier = None
        for tier in ALL_TIERS:
            meets, failure_details = RiskTierClassifier._check_criteria_detailed(
                metrics, tier
            )
            if meets:
                best_eligible_tier = tier
                logger.info(
                    f"✅ {metrics.symbol}: Selected tier {tier.name} (trades={metrics.total_trades}, PF={metrics.profit_factor:.2f})"
                )
                break
            elif failure_details and failure_details.get("pf_stability_gated"):
                # Log PF stability gating if it was the reason for rejection on a higher tier
                logger.debug(
                    f"🔒 {metrics.symbol}: Tier {tier.name} gated by PF stability "
                    f"(required {failure_details['threshold']} trades, has {failure_details['value']})"
                )

        # If no tier qualifies, fall back to PROBATION
        if best_eligible_tier is None:
            logger.warning(
                f"⚠️ {metrics.symbol}: No tier qualified! Falling back to PROBATION (trades={metrics.total_trades}, PF={metrics.profit_factor:.2f})"
            )
            logger.warning(f"⚠️ {metrics.symbol}: win_rate={metrics.win_rate:.2f}")
            best_eligible_tier = probation_tier

        # If there's no current tier (first classification), return best eligible
        if current_tier_obj is None:
            return best_eligible_tier

        # Apply hysteresis: check if we should stay in current tier despite being eligible for another
        current_tier_index = ALL_TIERS.index(current_tier_obj)
        best_tier_index = ALL_TIERS.index(best_eligible_tier)

        # Only log hysteresis if tier is changing
        if current_tier_obj.name != best_eligible_tier.name:
            logger.info(
                f"🔄 {metrics.symbol}: Hysteresis check - Current: {current_tier_obj.name} (idx={current_tier_index}), "
                f"Eligible: {best_eligible_tier.name} (idx={best_tier_index}), trades_in_tier={trades_in_tier}"
            )

        # PROMOTION hysteresis (moving to higher tier, lower index in our sorted list)
        if best_tier_index < current_tier_index:
            # Check minimum stay requirement
            if min_stay_trades > 0 and trades_in_tier < min_stay_trades:
                logger.debug(
                    f"Promotion blocked by min_stay ({trades_in_tier}/{min_stay_trades} trades)"
                )
                return current_tier_obj

            # Check promotion buffer (PF must exceed target tier min by buffer amount)
            if (
                promote_buffer_pf > 0
                and best_eligible_tier.profit_factor_min is not None
            ):
                required_pf = best_eligible_tier.profit_factor_min + promote_buffer_pf
                if metrics.profit_factor < required_pf:
                    logger.debug(
                        f"Promotion buffer not met (PF {metrics.profit_factor:.2f} < {required_pf:.2f})"
                    )
                    return current_tier_obj

            # Check promote_after_trades (must have N trades since entering current tier)
            if promote_after_trades > 0 and trades_in_tier < promote_after_trades:
                logger.debug(
                    f"Promotion blocked by promote_after_trades ({trades_in_tier}/{promote_after_trades})"
                )
                return current_tier_obj

            # All promotion checks passed
            logger.info(
                f"🎉 {metrics.symbol}: Tier promotion {current_tier_obj.name} → {best_eligible_tier.name} "
                f"(PF={metrics.profit_factor:.2f}, trades_in_tier={trades_in_tier}, "
                f"current_consecutive_losses_lookback={metrics.current_consecutive_losses}, "
                f"max_consecutive_losses_lookback={metrics.max_consecutive_losses})"
            )
            return best_eligible_tier

        # DEMOTION hysteresis (moving to lower tier, higher index)
        elif best_tier_index > current_tier_index:
            # Check if symbol still qualifies for current tier
            still_qualifies, failure_details = (
                RiskTierClassifier._check_criteria_detailed(metrics, current_tier_obj)
            )

            if still_qualifies:
                # Symbol still meets current tier criteria, check demotion grace for consecutive losses
                if (
                    demote_after_losses > 0
                    and consecutive_losses_in_tier < demote_after_losses
                ):
                    logger.debug(
                        f"Demotion deferred (still qualifies for {current_tier_obj.name}, consecutive losses {consecutive_losses_in_tier}/{demote_after_losses})"
                    )
                    return current_tier_obj

                # Check demotion buffer: current PF must fall below (current tier min - buffer)
                # Only apply buffer check if current tier has a valid profit_factor_min
                if (
                    demote_buffer_pf > 0
                    and current_tier_obj.profit_factor_min is not None
                ):
                    threshold_pf = current_tier_obj.profit_factor_min - demote_buffer_pf
                    if metrics.profit_factor >= threshold_pf:
                        logger.debug(
                            f"Demotion buffer prevents drop (PF {metrics.profit_factor:.2f} >= {threshold_pf:.2f})"
                        )
                        return current_tier_obj
            else:
                # Strict criteria failed. Check if we are within buffer.
                if (
                    demote_buffer_pf > 0
                    and current_tier_obj.profit_factor_min is not None
                ):
                    # Only apply buffer if failure is due to PF
                    if (
                        failure_details
                        and failure_details.get("criterion") == "profit_factor_min"
                    ):
                        threshold_pf = (
                            current_tier_obj.profit_factor_min - demote_buffer_pf
                        )
                        if metrics.profit_factor >= threshold_pf:
                            logger.debug(
                                f"Demotion buffer prevents drop (PF {metrics.profit_factor:.2f} >= {threshold_pf:.2f})"
                            )
                            return current_tier_obj

            # Demotion confirmed - find the highest tier below current that qualifies
            demotion_target = None
            for tier in ALL_TIERS[
                current_tier_index + 1 :
            ]:  # Check tiers below current
                if RiskTierClassifier._meets_criteria(metrics, tier):
                    demotion_target = tier
                    break
            # If no lower tier qualifies, use PROBATION
            if demotion_target is None:
                demotion_target = probation_tier

            # Determine log message type
            if (
                demote_buffer_pf > 0
                and failure_details
                and failure_details.get("criterion") == "profit_factor_min"
            ):
                threshold_pf = current_tier_obj.profit_factor_min - demote_buffer_pf
                logger.info(
                    f"⚠️ {metrics.symbol}: Buffer demotion {current_tier_obj.name} → {demotion_target.name} "
                    f"(PF {metrics.profit_factor:.2f} < {threshold_pf:.2f}, "
                    f"current_consecutive_losses_lookback={metrics.current_consecutive_losses}, "
                    f"max_consecutive_losses_lookback={metrics.max_consecutive_losses})"
                )
            else:
                # Log explicit failure reason
                if failure_details:
                    fail_msg = failure_details.get("msg", "Unknown reason")
                else:
                    # If no failure details but we are demoting, it must be due to tier-scoped consecutive losses
                    fail_msg = f"Consecutive losses in tier ({consecutive_losses_in_tier}) >= limit ({demote_after_losses})"

                logger.info(
                    f"⚠️ {metrics.symbol}: Tier demotion {current_tier_obj.name} → {demotion_target.name} "
                    f"(Reason: {fail_msg}, PF={metrics.profit_factor:.2f}, consecutive_losses_tier={consecutive_losses_in_tier}, "
                    f"current_consecutive_losses_lookback={metrics.current_consecutive_losses}, "
                    f"max_consecutive_losses_lookback={metrics.max_consecutive_losses})"
                )

            return demotion_target

        # Same tier: check for hysteresis demotion
        still_qualifies, failure_details = RiskTierClassifier._check_criteria_detailed(
            metrics, current_tier_obj
        )
        if still_qualifies:
            # Check demotion grace for consecutive losses
            if (
                demote_after_losses > 0
                and consecutive_losses_in_tier >= demote_after_losses
            ):
                # Demotion confirmed - find the highest tier below current that qualifies
                demotion_target = None
                for tier in ALL_TIERS[
                    current_tier_index + 1 :
                ]:  # Check tiers below current
                    if RiskTierClassifier._meets_criteria(metrics, tier):
                        demotion_target = tier
                        break
                # If no lower tier qualifies, use PROBATION
                if demotion_target is None:
                    demotion_target = probation_tier

                logger.info(
                    f"⚠️ {metrics.symbol}: Hysteresis demotion {current_tier_obj.name} → {demotion_target.name} "
                    f"(still qualifies, but consecutive_losses_tier={consecutive_losses_in_tier} >= {demote_after_losses}, "
                    f"current_consecutive_losses_lookback={metrics.current_consecutive_losses}, "
                    f"max_consecutive_losses_lookback={metrics.max_consecutive_losses})"
                )
                return demotion_target

            # Check demotion buffer: current PF must fall below (current tier min - buffer)
            if demote_buffer_pf > 0 and current_tier_obj.profit_factor_min is not None:
                threshold_pf = current_tier_obj.profit_factor_min - demote_buffer_pf
                if metrics.profit_factor < threshold_pf:
                    # Demotion confirmed
                    demotion_target = None
                    for tier in ALL_TIERS[current_tier_index + 1 :]:
                        if RiskTierClassifier._meets_criteria(metrics, tier):
                            demotion_target = tier
                            break
                    if demotion_target is None:
                        demotion_target = probation_tier

                    logger.info(
                        f"⚠️ {metrics.symbol}: Buffer demotion {current_tier_obj.name} → {demotion_target.name} "
                        f"(PF {metrics.profit_factor:.2f} < {threshold_pf:.2f}, "
                        f"current_consecutive_losses_lookback={metrics.current_consecutive_losses}, "
                        f"max_consecutive_losses_lookback={metrics.max_consecutive_losses})"
                    )
                    return demotion_target

        logger.debug(
            f"Same tier, no hysteresis change - staying in {current_tier_obj.name}"
        )
        return current_tier_obj

    @staticmethod
    def _check_criteria_detailed(
        metrics: PerformanceMetrics, tier: RiskTier
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Check if metrics meet tier criteria, returning detailed failure reason if not.
        Returns (passed, failure_details)
        """
        if metrics.total_trades < tier.min_trades:
            return False, {
                "criterion": "min_trades",
                "value": metrics.total_trades,
                "threshold": tier.min_trades,
                "msg": f"Total trades {metrics.total_trades} < {tier.min_trades}",
            }

        # Strict max-exclusive for max_trades when provided
        if tier.max_trades is not None and metrics.total_trades >= tier.max_trades:
            return False, {
                "criterion": "max_trades",
                "value": metrics.total_trades,
                "threshold": tier.max_trades,
                "msg": f"Total trades {metrics.total_trades} >= {tier.max_trades}",
            }

        # Profit factor stability: optionally require minimum total trades
        pf_min_trades = SETTINGS.get("pf_min_trades_for_validation")
        if (
            pf_min_trades is not None
            and metrics.total_trades < pf_min_trades
            and tier.profit_factor_min >= 1.2
        ):
            # For higher tiers (STANDARD and above, PF>=1.2), require sufficient sample
            return False, {
                "criterion": "pf_stability",
                "value": metrics.total_trades,
                "threshold": pf_min_trades,
                "pf_stability_gated": True,
                "msg": f"PF stability check failed: {metrics.total_trades} total trades < {pf_min_trades} required",
            }

        # Min inclusive
        if metrics.profit_factor < tier.profit_factor_min:
            return False, {
                "criterion": "profit_factor_min",
                "value": metrics.profit_factor,
                "threshold": tier.profit_factor_min,
                "msg": f"PF {metrics.profit_factor:.2f} < {tier.profit_factor_min:.2f}",
            }

        # Max exclusive when specified
        if (
            tier.profit_factor_max is not None
            and metrics.profit_factor >= tier.profit_factor_max
        ):
            return False, {
                "criterion": "profit_factor_max",
                "value": metrics.profit_factor,
                "threshold": tier.profit_factor_max,
                "msg": f"PF {metrics.profit_factor:.2f} >= {tier.profit_factor_max:.2f}",
            }

        # Important: Do NOT disqualify for exceeding profit_factor_max.
        # A very high PF should not prevent a symbol from qualifying for a lower tier
        # when it lacks the trade history for higher tiers.

        if metrics.sharpe_ratio < tier.sharpe_ratio_min:
            return False, {
                "criterion": "sharpe_ratio_min",
                "value": metrics.sharpe_ratio,
                "threshold": tier.sharpe_ratio_min,
                "msg": f"Sharpe {metrics.sharpe_ratio:.2f} < {tier.sharpe_ratio_min:.2f}",
            }

        if tier.win_rate_min and metrics.win_rate < tier.win_rate_min:
            return False, {
                "criterion": "win_rate_min",
                "value": metrics.win_rate,
                "threshold": tier.win_rate_min,
                "msg": f"Win Rate {metrics.win_rate:.2f} < {tier.win_rate_min:.2f}",
            }

        # Drawdown remains max-inclusive guard (equals allowed)
        if tier.max_drawdown_max and metrics.max_drawdown > tier.max_drawdown_max:
            return False, {
                "criterion": "max_drawdown_max",
                "value": metrics.max_drawdown,
                "threshold": tier.max_drawdown_max,
                "msg": f"Max DD {metrics.max_drawdown:.2f} > {tier.max_drawdown_max:.2f}",
            }

        if (
            tier.consecutive_losses_max
            and metrics.max_consecutive_losses > tier.consecutive_losses_max
        ):
            return False, {
                "criterion": "consecutive_losses_max",
                "value": metrics.max_consecutive_losses,
                "threshold": tier.consecutive_losses_max,
                "msg": f"Max Consec Losses {metrics.max_consecutive_losses} > {tier.consecutive_losses_max}",
            }

        return True, None

    @staticmethod
    def _meets_criteria(metrics: PerformanceMetrics, tier: RiskTier) -> bool:
        passed, _ = RiskTierClassifier._check_criteria_detailed(metrics, tier)
        return passed


class PositionSizer:
    @staticmethod
    def calculate_position_size(
        capital: float,
        tier: RiskTier,
        metrics: PerformanceMetrics,
        current_price: float,
        atr: float,
    ) -> Dict[str, Any]:
        base_allocation = capital * tier.capital_allocation
        dd_multiplier = PositionSizer._calculate_drawdown_multiplier(metrics, tier.name)
        adjusted_allocation = base_allocation * dd_multiplier
        kelly_leverage = PositionSizer._calculate_kelly_leverage(
            metrics.win_rate,
            metrics.avg_win,
            metrics.avg_loss,
            metrics.avg_win_r,
            tier.max_leverage,
            atr,
            current_price,
            metrics.profit_factor,
        )

        # Apply tier leverage band and quantize to exchange-compatible integer
        # leverage. Round is used to preserve intent better than truncation.
        bounded_leverage = max(
            float(tier.min_leverage),
            min(float(kelly_leverage), float(tier.max_leverage)),
        )
        final_leverage = int(round(bounded_leverage))

        notional = adjusted_allocation * final_leverage
        position_size = notional / current_price if current_price > 0 else 0.0
        if tier.max_position_size_usd and notional > tier.max_position_size_usd:
            notional = tier.max_position_size_usd
            position_size = notional / current_price if current_price > 0 else 0.0

        # Determine leverage source for transparency
        leverage_source = "Composite"
        if final_leverage == tier.min_leverage and kelly_leverage < tier.min_leverage:
            leverage_source = f"Tier Min (Composite was {kelly_leverage:.2f}x)"
        elif final_leverage == tier.max_leverage and kelly_leverage > tier.max_leverage:
            leverage_source = f"Tier Max (Composite was {kelly_leverage:.2f}x)"

        kelly_reason = (
            f"Composite: WR={metrics.win_rate * 100:.1f}%, "
            f"PF={metrics.profit_factor:.2f}, "
            f"AvgWinR={metrics.avg_win_r:.2f}R "
            f"-> {kelly_leverage:.2f}x, {leverage_source} -> {final_leverage}x"
        )

        return {
            "allowed": True,
            "tier": tier.name,
            "capital_allocation_pct": tier.capital_allocation * 100,
            "leverage": final_leverage,
            "notional": notional,
            "position_size": position_size,
            "base_allocation": base_allocation,
            "drawdown_mult": dd_multiplier,
            "kelly_leverage": kelly_leverage,
            "tier_min_leverage": tier.min_leverage,
            "tier_max_leverage": tier.max_leverage,
            "kelly_reason": kelly_reason,
            "reasoning": f"{tier.name} tier: {tier.capital_allocation * 100}% capital @ {final_leverage}x leverage",
        }

    @staticmethod
    def _calculate_drawdown_multiplier(
        metrics: PerformanceMetrics, tier_name: str
    ) -> float:
        if not SETTINGS.get("enable_drawdown_scaling", True):
            return 1.0
        dd_pct = metrics.current_drawdown_pct * 100.0  # convert to %
        # Per-tier override takes precedence
        overrides = SETTINGS.get("drawdown_scaling_overrides", {}) or {}
        if tier_name in overrides:
            ov = overrides[tier_name]
            threshold = float(
                ov.get(
                    "threshold_pct", SETTINGS.get("drawdown_scale_threshold_pct", 10.0)
                )
            )
            factor = float(ov.get("factor", SETTINGS.get("drawdown_scale_factor", 0.5)))
            return 1.0 if dd_pct <= threshold else max(0.0, min(1.0, factor))
        # Global threshold/factor
        threshold = float(SETTINGS.get("drawdown_scale_threshold_pct", 10.0))
        factor = float(SETTINGS.get("drawdown_scale_factor", 0.5))
        return 1.0 if dd_pct <= threshold else max(0.0, min(1.0, factor))

    @staticmethod
    def _calculate_kelly_leverage(
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        avg_win_r: float,
        max_leverage: int,
        atr: float = 0.0,
        current_price: float = 0.0,
        profit_factor: float = 0.0,
    ) -> int:
        """
        Calculate leverage using a composite score of Profit Factor, Win Rate,
        and traditional Kelly criterion.

        The composite formula is designed for strategies that capture small,
        frequent wins (high WR, low R-multiples) where standard Kelly
        under-weights proven edge. Components:

          - Profit Factor score (50%): min(PF, 5.0) / 5.0
          - Win Rate score (45%): max(0, (WR - 0.5)) / 0.5
          - Kelly score (5%): traditional kelly_f clamped to [0, 1]

        Result is half-sized (× 0.5) and scaled by max_leverage.

        Args:
            win_rate: Win rate as fraction [0, 1].
            avg_win: Average win in USD.
            avg_loss: Average loss in USD.
            avg_win_r: Average win in R-multiples.
            max_leverage: Tier maximum leverage.
            atr: Average true range (unused in composite, kept for API compat).
            current_price: Current price (unused in composite, kept for API compat).
            profit_factor: Gross profit / gross loss ratio.

        Returns:
            Integer leverage value in [0, max_leverage].
        """
        # Edge guards — same safe defaults as before
        if win_rate >= 1.0:
            return int(max_leverage)

        if win_rate <= 0:
            return 0

        # --- Component 1: Profit Factor score (weight 0.50) ---
        pf_cap = 5.0
        pf_score = min(max(profit_factor, 0.0), pf_cap) / pf_cap

        # --- Component 2: Win Rate score (weight 0.30) ---
        # Maps WR from [0.5, 1.0] -> [0, 1]. Below 50% WR scores 0.
        wr_score = max(0.0, (win_rate - 0.5)) / 0.5

        atr_score = atr / (atr + 1) if atr else 0

        # --- Component 3: Kelly score (weight 0.20) ---
        # Traditional kelly_f, clamped to [0, 1]
        if avg_win_r > 0:
            kelly_f = win_rate * avg_win_r - (1 - win_rate)
        elif avg_loss > 0 and avg_win > 0:
            b = avg_win / avg_loss
            kelly_f = (win_rate * b - (1 - win_rate)) / b
        else:
            kelly_f = 0.0
        kelly_score = min(1.0, max(0.0, kelly_f))

        # --- Composite ---
        w_pf = 0.50
        w_wr = 0.45
        w_kelly = 0.05
        composite = (w_pf * pf_score) + (w_wr * wr_score) + (w_kelly * kelly_score)

        leverage_raw = float(max(0.0, composite) * max_leverage)

        leverage_quantized = int(round(leverage_raw))
        logger.debug(
            f"Kelly leverage breakdown: pf_score={pf_score:.2f}, wr_score={wr_score:.2f}, kelly_score={kelly_score:.2f}, "
            f"composite={composite:.2f}, leverage_raw={leverage_raw:.2f}"
        )

        return leverage_quantized


class PortfolioRiskMonitor:
    MAX_PORTFOLIO_HEAT = 0.08
    MAX_CONCURRENT_POSITIONS = 6

    @staticmethod
    def check_portfolio_heat(
        active_positions: List[Dict[str, Any]],
        proposed_risk: float,
        total_capital: float,
    ) -> Tuple[bool, float, str]:
        current_risk = sum(
            float(pos.get("initial_risk_atr", 0)) * float(pos.get("initial_amount", 0))
            for pos in active_positions
        )
        total_risk = current_risk + proposed_risk
        if total_capital <= 0:
            return False, 0.0, "Total capital is zero"
        heat_ratio = total_risk / total_capital
        # Use configurable heat thresholds (as ratio of capital)
        max_heat_ratio = float(SETTINGS.get("max_portfolio_heat_pct", 8.0)) / 100.0
        soft_band = max(
            0.0, min(max_heat_ratio, max_heat_ratio * 0.75)
        )  # 75% of max as soft band
        if heat_ratio <= soft_band:
            return True, 1.0, "Portfolio heat optimal"
        elif heat_ratio <= max_heat_ratio:
            # Linearly scale down up to 50% as we approach the cap
            span = max(1e-6, max_heat_ratio - soft_band)
            scale = (heat_ratio - soft_band) / span
            multiplier = max(0.5, 1.0 - 0.5 * scale)
            return True, multiplier, f"Portfolio heat elevated: {heat_ratio * 100:.1f}%"
        else:
            multiplier = max(0.25, max_heat_ratio / max(1e-6, heat_ratio))
            return (
                True,
                multiplier,
                f"Portfolio heat high: {heat_ratio * 100:.1f}%, reducing position",
            )


class KillSwitch:
    @staticmethod
    def check_triggers(metrics: PerformanceMetrics) -> Tuple[bool, str]:
        if not SETTINGS.get("enable_kill_switch", True):
            return False, ""
        dd_limit = float(SETTINGS.get("kill_switch_drawdown_pct", 30.0))
        cons_losses_limit = int(SETTINGS.get("kill_switch_consecutive_losses", 7))
        if (metrics.current_drawdown_pct * 100.0) > dd_limit:
            return (
                True,
                f"Drawdown {metrics.current_drawdown_pct * 100:.1f}% exceeds {dd_limit:.0f}% limit",
            )
        if metrics.current_consecutive_losses >= cons_losses_limit:
            return (
                True,
                f"{metrics.current_consecutive_losses} consecutive losses (limit {cons_losses_limit})",
            )
        # Optional PF kill switch now configurable
        pf_min_trades = int(SETTINGS.get("kill_switch_pf_min_trades", 30))
        if metrics.total_trades >= pf_min_trades and metrics.profit_factor < 0.6:
            return (
                True,
                f"Profit Factor {metrics.profit_factor:.2f} below 0.5 threshold (after {pf_min_trades}+ trades)",
            )
        return False, ""


class AdaptiveRiskManager:
    def __init__(
        self,
        data_dir: Path = Path("data_futures"),
        config_path: Optional[Path] = None,
        capital_manager: Optional[Any] = None,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        self.performance_file = self.data_dir / "symbol_performance.json"
        self.performance_cache: Dict[str, PerformanceMetrics] = {}
        self.tier_cache: Dict[str, RiskTier] = {}
        self.kill_switch_active: Dict[str, bool] = {}
        # Use CapitalManager for tier storage (single source of truth)
        self.capital_manager = capital_manager
        # If a specific config is provided, reload tiers/settings for this process
        try:
            if config_path is not None and config_path.exists():
                global ALL_TIERS
                ALL_TIERS = load_risk_tiers_from_config(str(config_path))
                logger.info(f"AdaptiveRiskManager using config: {config_path}")
        except Exception:
            logger.error(
                "Failed to load tiers/settings from provided config_path; continuing with defaults",
                exc_info=True,
            )
        self._load_state()
        logger.info("Adaptive Risk Manager initialized")

    def calculate_position_parameters(
        self,
        symbol: str,
        capital: float,
        current_price: float,
        atr: float,
        trade_history: List[Dict[str, Any]],
        active_positions: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        # Tier history now managed by CapitalManager (always up-to-date)
        if self.kill_switch_active.get(symbol, False):
            cached_tier = self.tier_cache.get(symbol)
            current_tier_name = cached_tier.name if cached_tier else "PROBATION"
            return {
                "allowed": False,
                "reason": "Kill switch active",
                "tier": current_tier_name,
                "kill_switch_active": True,
            }
        metrics = PerformanceAnalyzer.calculate_metrics(
            symbol,
            trade_history,
            lookback_trades=int(SETTINGS.get("lookback_trades", 30)),
            initial_capital=capital,
        )
        logger.info(
            f"📊 {symbol}: Calculated metrics - trades={metrics.total_trades}, "
            f"PF={metrics.profit_factor:.2f}, WR={metrics.win_rate * 100:.1f}%"
        )
        triggered, reason = KillSwitch.check_triggers(metrics)
        if triggered:
            self.kill_switch_active[symbol] = True
            self._save_state()
            cached_tier = self.tier_cache.get(symbol)
            current_tier_name = cached_tier.name if cached_tier else "PROBATION"
            return {
                "allowed": False,
                "reason": f"Kill switch triggered: {reason}",
                "tier": current_tier_name,
                "kill_switch_active": True,
            }

        # Get tier history for hysteresis from CapitalManager (single source of truth)
        tier_hist = self._get_tier_history_sync(symbol) if self.capital_manager else {}
        logger.info(
            f"📜 {symbol}: Tier history - current_tier={tier_hist.get('current_tier')}, trades_in_tier={tier_hist.get('trades_in_tier')}"
        )

        # Classify with hysteresis
        tier = RiskTierClassifier.classify(metrics, tier_hist)
        logger.info(f"🎯 {symbol}: Classified as {tier.name}")

        # Update tier history
        old_tier_name = tier_hist.get("current_tier")
        tier_changed = False
        if old_tier_name != tier.name:
            # Tier changed: reset counters
            tier_changed = True
            new_tier_data = {
                "current_tier": tier.name,
                "tier_entry_time": datetime.now(timezone.utc).isoformat(),
                "trades_in_tier": 0,
                "consecutive_losses_in_tier": 0,
                "last_transition_time": datetime.now(timezone.utc).isoformat(),
                "previous_tier": old_tier_name,
                "last_total_trades": 0,
            }
            if self.capital_manager:
                self._update_tier_history_sync(symbol, new_tier_data)
            logger.info(
                f"🔄 {symbol}: Tier transition detected: {old_tier_name} → {tier.name}"
            )
        else:
            # Same tier: increment counters
            # Increment trades_in_tier if total_trades increased
            prev_total = tier_hist.get("last_total_trades", 0)
            trades_in_tier = tier_hist.get("trades_in_tier", 0)
            if metrics.total_trades > prev_total:
                trades_in_tier = int(trades_in_tier) + 1

            # Update consecutive losses
            consecutive_losses = 0
            if metrics.current_consecutive_losses > 0:
                consecutive_losses = metrics.current_consecutive_losses

            # Update tier history in CapitalManager
            updated_tier_data = {
                "current_tier": tier.name,
                "tier_entry_time": tier_hist.get(
                    "tier_entry_time", datetime.now(timezone.utc).isoformat()
                ),
                "trades_in_tier": trades_in_tier,
                "consecutive_losses_in_tier": consecutive_losses,
                "last_transition_time": tier_hist.get(
                    "last_transition_time", datetime.now(timezone.utc).isoformat()
                ),
                "previous_tier": tier_hist.get("previous_tier"),
                "last_total_trades": metrics.total_trades,
            }
            if self.capital_manager:
                self._update_tier_history_sync(symbol, updated_tier_data)

        self.performance_cache[symbol] = metrics
        self.tier_cache[symbol] = tier
        position_params = PositionSizer.calculate_position_size(
            capital, tier, metrics, current_price, atr
        )
        if active_positions is not None:
            proposed_risk = atr * position_params["position_size"]

            # Dynamic portfolio capital calculation
            if self.capital_manager:
                all_caps = self.capital_manager.get_all_capitals()
                portfolio_capital = (
                    float(sum(all_caps.values())) if all_caps else capital * 6
                )
            else:
                portfolio_capital = capital * 6

            if portfolio_capital <= 0:
                portfolio_capital = capital * 6

            allowed, heat_mult, heat_reason = PortfolioRiskMonitor.check_portfolio_heat(
                active_positions, proposed_risk, portfolio_capital
            )
            if not allowed:
                return {"allowed": False, "reason": heat_reason, "tier": tier.name}
            if heat_mult < 1.0:
                position_params["position_size"] *= heat_mult
                position_params["notional"] *= heat_mult
                position_params["portfolio_heat_mult"] = heat_mult
                position_params["reasoning"] += f" (heat adjusted: {heat_mult:.2f}x)"
            # Optional: enforce per-tier portfolio caps if configured (best-effort using initial notional)
            tier_caps = SETTINGS.get("tier_portfolio_caps", {}) or {}
            cap_pct = tier_caps.get(tier.name)
            if cap_pct is not None and cap_pct > 0:
                # Approximate current tier exposure using initial_amount*entry_price if available
                total_notional = 0.0
                for pos in active_positions:
                    sym = pos.get("symbol") or pos.get("symbol_id")
                    if not sym:
                        continue
                    pos_tier = self.tier_cache.get(sym)
                    if not pos_tier or pos_tier.name != tier.name:
                        continue
                    amount = float(pos.get("initial_amount", 0) or 0)
                    entry_price = float(pos.get("entry_price", 0) or 0)
                    total_notional += amount * entry_price
                cap_notional = (cap_pct / 100.0) * capital
                if (
                    total_notional + position_params["notional"]
                ) > cap_notional and cap_notional > 0:
                    # Scale down to fit cap
                    available = max(0.0, cap_notional - total_notional)
                    scale = (
                        available / max(1e-6, position_params["notional"])
                        if position_params["notional"] > 0
                        else 0.0
                    )
                    position_params["position_size"] *= max(0.0, min(1.0, scale))
                    position_params["notional"] = available
                    position_params["reasoning"] += (
                        f" (tier cap {cap_pct:.0f}% applied)"
                    )
        position_params["metrics"] = {
            "profit_factor": metrics.profit_factor,
            "sharpe_ratio": metrics.sharpe_ratio,
            "win_rate": metrics.win_rate,
            "max_drawdown": metrics.max_drawdown,
            "total_trades": metrics.total_trades,
        }
        position_params["tier_changed"] = tier_changed
        position_params["old_tier"] = old_tier_name
        self._save_state()
        return position_params

    def get_tier_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get tier info for a symbol.
        Always reads from CapitalManager (single source of truth) to ensure consistency.
        """
        # Get tier from CapitalManager (single source of truth)
        tier_data = self._get_tier_history_sync(symbol) if self.capital_manager else {}
        tier_name = tier_data.get("current_tier", "PROBATION")

        # Find tier object by name
        tier = next((t for t in ALL_TIERS if t.name == tier_name), ALL_TIERS[-1])

        # Update cache to keep it in sync
        self.tier_cache[symbol] = tier

        # Get metrics
        metrics = self.performance_cache.get(symbol)

        return {
            "tier": tier.name,
            "capital_allocation": tier.capital_allocation,
            "max_leverage": tier.max_leverage,
            "description": tier.description,
            "metrics": asdict(metrics) if metrics else None,
            "kill_switch_active": self.kill_switch_active.get(symbol, False),
        }

    def reset_kill_switch(self, symbol: str) -> None:
        self.kill_switch_active[symbol] = False
        self._save_state()
        logger.info(f"Kill switch reset for {symbol}")

    def _load_state(self) -> None:
        try:
            if self.performance_file.exists():
                with open(self.performance_file) as f:
                    data = json.load(f)
                    for symbol, metrics_dict in data.items():
                        if "avg_win_r" not in metrics_dict:
                            metrics_dict["avg_win_r"] = 0.0
                        self.performance_cache[symbol] = PerformanceMetrics(
                            **metrics_dict
                        )
        except Exception as e:
            logger.warning(f"Failed to load performance cache: {e}")

        if not self.performance_file.exists():
            self._save_state()
            logger.info("Initialized symbol_performance.json")

        # Tier history now managed by CapitalManager (no separate file loading needed)

    def _get_tier_history_sync(self, symbol: str) -> Dict[str, Any]:
        """Synchronous wrapper for get_tier_history to work in sync context"""
        import asyncio

        try:
            # Try to get running loop
            asyncio.get_running_loop()
            # We're in an async context, create a task
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run, self.capital_manager.get_tier_history(symbol)
                )
                return future.result(timeout=5)
        except RuntimeError:
            # No running loop, can use asyncio.run safely
            return asyncio.run(self.capital_manager.get_tier_history(symbol))

    def _update_tier_history_sync(self, symbol: str, tier_data: Dict[str, Any]) -> None:
        """Synchronous wrapper for update_tier_history to work in sync context"""
        import asyncio

        try:
            # Try to get running loop
            asyncio.get_running_loop()
            # We're in an async context, create a task
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    self.capital_manager.update_tier_history(symbol, tier_data),
                )
                future.result(timeout=5)
        except RuntimeError:
            # No running loop, can use asyncio.run safely
            asyncio.run(self.capital_manager.update_tier_history(symbol, tier_data))

    def _save_state(self) -> None:
        """Save state using atomic writes to prevent file corruption."""
        import shutil
        import tempfile

        try:
            # Write performance file atomically
            temp_perf = tempfile.NamedTemporaryFile(
                mode="w", dir=self.performance_file.parent, delete=False, suffix=".tmp"
            )
            try:
                # Convert dataclasses to dicts, ensuring datetime fields are strings
                perf_data = {}
                for k, v in self.performance_cache.items():
                    v_dict = asdict(v)
                    # Ensure all datetime-like fields are strings
                    if "last_calculated" in v_dict and not isinstance(
                        v_dict["last_calculated"], str
                    ):
                        v_dict["last_calculated"] = str(v_dict["last_calculated"])
                    if (
                        "first_trade_date" in v_dict
                        and v_dict["first_trade_date"]
                        and not isinstance(v_dict["first_trade_date"], str)
                    ):
                        v_dict["first_trade_date"] = str(v_dict["first_trade_date"])
                    perf_data[k] = v_dict

                json.dump(perf_data, temp_perf, indent=2)
                temp_perf.flush()
                temp_perf.close()
                shutil.move(temp_perf.name, self.performance_file)
            except Exception as e:
                temp_perf.close()
                Path(temp_perf.name).unlink(missing_ok=True)
                raise e

            # Tier history now saved by CapitalManager (no separate file needed)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")


def create_risk_manager(
    data_dir: str = "data_futures", config_path: Optional[str] = None
) -> AdaptiveRiskManager:
    return AdaptiveRiskManager(
        data_dir=Path(data_dir), config_path=Path(config_path) if config_path else None
    )


# ----------------------------
# Hot Reload Utilities
# ----------------------------
def _safe_load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


# Hot-reload logic removed - tier history now managed by CapitalManager
# (single source of truth in symbol_capitals.json)
