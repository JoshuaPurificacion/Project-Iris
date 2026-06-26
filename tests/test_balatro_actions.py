from unittest.mock import MagicMock, patch

from skills.balatro.actions.base import ActionContext
from skills.balatro.actions.utility import SellJokerAction
from skills.balatro.actions.shop import BuyShopAction
from skills.balatro.actions.shop import RerollAction
from skills.balatro.actions.continue_action import ContinueAction
from skills.balatro.actions.play import PlayHandAction


def _build_context(raw_state: dict, indices: list[int]) -> ActionContext:
    session = MagicMock()
    session.run_profile = {}

    tick_context = MagicMock()
    tick_context.raw_state = raw_state

    client = MagicMock()
    client.last_error = ""

    controller = MagicMock()
    controller.client = client

    return ActionContext(
        session=session,
        tick_context=tick_context,
        planner_output={"indices": indices, "reasoning": "test"},
        controller=controller,
        state=(raw_state.get("state") or "").lower(),
    )


def test_sell_joker_blocked_when_state_disallows_sell():
    raw_state = {
        "state": "round_eval",
        "jokers": {"cards": [{"label": "Test Joker", "key": "j_test"}]},
        "hand": {"cards": []},
    }
    ctx = _build_context(raw_state, [1])

    action = SellJokerAction()
    result = action.execute(ctx)

    assert result.succeeded is False
    assert "blocked in state" in (result.error or "")
    ctx.controller.client.sell.assert_not_called()


@patch("skills.balatro.actions.utility.BalatroAlgorithm.identify_safe_sell_jokers")
def test_sell_joker_includes_api_error_detail(mock_safe_sell):
    mock_safe_sell.return_value = [{"index": 1, "loss_pct": 0.0}]

    raw_state = {
        "state": "shop",
        "jokers": {
            "size": 5,
            "cards": [
                {"label": "Test Joker", "key": "j_test"},
                {"label": "Joker 2", "key": "j_test_2"},
                {"label": "Joker 3", "key": "j_test_3"},
                {"label": "Joker 4", "key": "j_test_4"},
                {"label": "Joker 5", "key": "j_test_5"},
            ]
        },
        "shop": {"cards": []},
        "hand": {"cards": []},
    }
    ctx = _build_context(raw_state, [1])
    ctx.controller.client.sell.return_value = False
    ctx.controller.client.last_error = "INVALID_STATE: Method 'sell' requires one of these states: SELECTING_HAND, SHOP"

    action = SellJokerAction()
    result = action.execute(ctx)

    assert result.succeeded is False
    assert "INVALID_STATE" in (result.error or "")


def test_continue_round_eval_does_not_force_next_round_on_cash_out_failure():
    raw_state = {"state": "round_eval"}
    ctx = _build_context(raw_state, [])

    # cash_out fails all retries
    ctx.controller.client._call.return_value = None
    ctx.controller.client.last_error = "INVALID_STATE: cash_out unavailable"

    action = ContinueAction()
    result = action.execute(ctx)

    assert result.succeeded is False
    assert "cash_out failed" in (result.error or "")
    ctx.controller.client.proceed_next.assert_not_called()


def test_buy_shop_bad_synergy_returns_graceful_denial_payload():
    raw_state = {
        "state": "shop",
        "money": 50,
        "ante_num": 3,
        "shop": {
            "cards": [
                {
                    "key": "j_half",
                    "label": "Half Joker",
                    "cost": 4,
                    "value": {"effect": "+20 Mult if played hand has 3 or fewer cards"},
                }
            ]
        },
        "jokers": {
            "size": 5,
            "cards": [
                {
                    "key": "j_flush_scaler",
                    "label": "Flush Scaler",
                    "value": {"effect": "+40 Mult when hand is a Flush"},
                }
            ],
        },
        "hand": {
            "cards": [
                {"value": {"rank": "A", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "K", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "Q", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "J", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "9", "suit": "Spades", "effect": ""}},
            ]
        },
        "packs": {"cards": []},
        "vouchers": {"cards": []},
    }

    ctx = _build_context(raw_state, [1])
    ctx.session.run_profile = {"deck": "CHECKERED"}

    action = BuyShopAction()
    result = action.execute(ctx)

    assert result.succeeded is False
    assert (
        result.error
        == "Action Denied: Purchase blocked by safety override (BAD SYNERGY). Pick a different action."
    )
    ctx.controller.client.buy.assert_not_called()


@patch("skills.balatro.actions.play.BalatroAlgorithm.auto_optimize_jokers")
def test_play_hand_runs_auto_optimize_only_in_play_phase(mock_optimize):
    mock_optimize.return_value = {
        "ordered_jokers": [{"label": "B"}, {"label": "A"}],
        "reorder_indices": [2, 1],
        "changed": True,
        "score": 123.0,
    }

    raw_state = {
        "state": "selecting_hand",
        "hand": {
            "cards": [
                {"value": {"rank": "A", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "K", "suit": "Spades", "effect": ""}},
            ]
        },
        "jokers": {
            "cards": [
                {"label": "A", "key": "j_a", "value": {"effect": "+10 Mult"}},
                {"label": "B", "key": "j_b", "value": {"effect": "X2 Mult"}},
            ]
        },
    }
    ctx = _build_context(raw_state, [1])
    ctx.controller.client.rearrange.return_value = True
    ctx.controller.client.play_hand.return_value = (True, None)
    ctx.controller.refresh_state.return_value = True

    action = PlayHandAction()
    result = action.execute(ctx)

    assert result.succeeded is True
    mock_optimize.assert_called_once()
    assert ctx.controller.client.rearrange.called
    assert ctx.controller.refresh_state.call_count >= 1


@patch("skills.balatro.actions.play.BalatroAlgorithm.auto_optimize_jokers")
def test_shop_action_does_not_call_auto_optimize(mock_optimize):
    raw_state = {
        "state": "shop",
        "money": 50,
        "ante_num": 2,
        "shop": {
            "cards": [
                {
                    "key": "j_mult",
                    "label": "Mult Joker",
                    "cost": 5,
                    "value": {"effect": "+20 Mult"},
                }
            ]
        },
        "jokers": {"size": 5, "cards": []},
        "hand": {"cards": []},
        "packs": {"cards": []},
        "vouchers": {"cards": []},
    }
    ctx = _build_context(raw_state, [1])
    ctx.session.run_profile = {"deck": "CHECKERED"}

    action = BuyShopAction()
    _ = action.execute(ctx)

    mock_optimize.assert_not_called()


def test_buy_shop_joker_slot_full_error_is_contextual():
    raw_state = {
        "state": "shop",
        "money": 30,
        "shop": {
            "cards": [
                {
                    "key": "j_new",
                    "label": "New Joker",
                    "cost": 6,
                    "value": {"effect": "+20 Mult"},
                }
            ]
        },
        "jokers": {
            "size": 5,
            "cards": [
                {"key": "j_a", "label": "A", "value": {"effect": "+10 Mult"}},
                {"key": "j_b", "label": "B", "value": {"effect": "+10 Mult"}},
                {"key": "j_c", "label": "C", "value": {"effect": "+10 Mult"}},
                {"key": "j_d", "label": "D", "value": {"effect": "+10 Mult"}},
                {"key": "j_e", "label": "E", "value": {"effect": "+10 Mult"}},
            ],
        },
        "hand": {"cards": []},
        "packs": {"cards": []},
        "vouchers": {"cards": []},
    }
    ctx = _build_context(raw_state, [1])
    action = BuyShopAction()
    result = action.execute(ctx)

    assert result.succeeded is False
    assert "Joker slots full" in (result.error or "")
    assert "sell_joker" in (result.error or "")


@patch("skills.balatro.actions.shop.BalatroAlgorithm.identify_safe_sell_jokers")
def test_buy_shop_joker_slot_full_error_includes_safe_sell_hint(mock_safe_sells):
    mock_safe_sells.return_value = [
        {
            "index": 2,
            "label": "Old Joker",
            "loss_pct": 3.5,
            "combined_loss_abs": 400,
            "reason": "LOW IMPACT",
        }
    ]

    raw_state = {
        "state": "shop",
        "money": 30,
        "shop": {
            "cards": [
                {
                    "key": "j_new",
                    "label": "New Joker",
                    "cost": 6,
                    "value": {"effect": "+20 Mult"},
                }
            ]
        },
        "jokers": {
            "size": 5,
            "cards": [
                {"key": "j_a", "label": "A", "value": {"effect": "+10 Mult"}},
                {"key": "j_b", "label": "B", "value": {"effect": "+10 Mult"}},
                {"key": "j_c", "label": "C", "value": {"effect": "+10 Mult"}},
                {"key": "j_d", "label": "D", "value": {"effect": "+10 Mult"}},
                {"key": "j_e", "label": "E", "value": {"effect": "+10 Mult"}},
            ],
        },
        "hand": {
            "cards": [
                {"value": {"rank": "A", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "K", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "Q", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "J", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "9", "suit": "Spades", "effect": ""}},
            ]
        },
        "packs": {"cards": []},
        "vouchers": {"cards": []},
    }
    ctx = _build_context(raw_state, [1])
    action = BuyShopAction()
    result = action.execute(ctx)

    assert result.succeeded is False
    assert "Math suggests selling 'Old Joker'" in (result.error or "")
    assert "~-400 pts expected drop" in (result.error or "")
    mock_safe_sells.assert_called_once()
    kwargs = mock_safe_sells.call_args.kwargs
    assert kwargs.get("deck_state") is raw_state


@patch("skills.balatro.actions.shop._build_dynamic_slot_full_error", return_value=None)
def test_buy_shop_slot_full_api_failure_does_not_recompute_preflight(mock_slot_error):
    raw_state = {
        "state": "shop",
        "money": 20,
        "ante_num": 5,
        "shop": {
            "cards": [
                {
                    "key": "c_jupiter",
                    "label": "Jupiter",
                    "cost": 3,
                    "value": {"effect": "Level up Flush"},
                }
            ]
        },
        "jokers": {"size": 5, "cards": []},
        "hand": {"cards": []},
        "packs": {"cards": []},
        "vouchers": {"cards": []},
    }
    ctx = _build_context(raw_state, [1])
    ctx.controller.client.buy.return_value = False
    ctx.controller.client._last_error = "Slots are full"

    action = BuyShopAction()
    result = action.execute(ctx)

    assert result.succeeded is False
    assert "slot constraints" in (result.error or "").lower()
    assert "api said" in (result.error or "").lower()
    assert mock_slot_error.call_count == 1
    ctx.controller.client.buy.assert_called_once()


def test_buy_shop_failed_purchase_refreshes_state_before_affordability_check():
    stale_state = {
        "state": "shop",
        "money": 10,
        "ante_num": 5,
        "shop": {
            "cards": [
                {
                    "key": "c_tarot_justice",
                    "label": "Justice",
                    "cost": 10,
                    "value": {"effect": "Enhances card"},
                },
                {
                    "key": "c_jupiter",
                    "label": "Jupiter",
                    "cost": 5,
                    "value": {"effect": "Level up Flush"},
                },
            ]
        },
        "jokers": {"size": 5, "cards": []},
        "hand": {"cards": []},
        "packs": {"cards": []},
        "vouchers": {"cards": []},
    }
    fresh_state = {
        **stale_state,
        "money": 0,
    }

    ctx = _build_context(stale_state, [1])
    ctx.controller.client.buy.return_value = False
    ctx.controller.client._last_error = "INSUFFICIENT_FUNDS"

    def _refresh_side_effect():
        ctx.controller.raw_state = fresh_state
        return True

    ctx.controller.refresh_state.side_effect = _refresh_side_effect

    action = BuyShopAction()
    result = action.execute(ctx)

    assert result.succeeded is False
    assert "Insufficient funds" in (result.error or "")
    ctx.controller.refresh_state.assert_called_once()
    ctx.tick_context.update_raw_state.assert_called_once_with(fresh_state)
    ctx.controller.client.proceed_next.assert_called_once()


@patch("skills.balatro.actions.shop._early_game_buy_block_reason", return_value=None)
@patch("skills.balatro.actions.shop._summarize_shop_item")
def test_buy_shop_uses_cached_summary_without_resummarizing(
    mock_summarize,
    _mock_block_reason,
):
    raw_state = {
        "state": "shop",
        "money": 12,
        "ante_num": 1,
        "shop": {
            "cards": [
                {
                    "key": "c_tarot_justice",
                    "label": "Justice",
                    "cost": 8,
                    "value": {"effect": "Enhances card"},
                }
            ]
        },
        "jokers": {"size": 5, "cards": []},
        "hand": {"cards": []},
        "packs": {"cards": []},
        "vouchers": {"cards": []},
    }
    ctx = _build_context(raw_state, [1])

    class _Cache:
        shop_cards = list(raw_state["shop"]["cards"])
        shop_summaries = [
            {
                "label": "Justice",
                "cost": 8,
                "advisor": "LOW VALUE",
                "economy_tag": "[COSTS INTEREST]",
                "impact_pct": 0.0,
                "baseline_score": 0,
                "projected_score": 0,
                "expected_score": 0,
                "immediate_impact": 0,
                "projected_3_round_impact": 0,
                "replace_idx": "",
                "replacement_index": None,
            }
        ]
        pack_cards = []
        pack_summaries = []
        voucher_cards = []
        voucher_summaries = []
        jokers = []

    ctx.tick_context.ensure_shop_cache.return_value = _Cache()
    ctx.controller.client.buy.return_value = False
    ctx.controller.client._last_error = "INVALID_ACTION"
    ctx.controller.refresh_state.return_value = False

    action = BuyShopAction()
    _ = action.execute(ctx)

    mock_summarize.assert_not_called()


@patch("skills.balatro.actions.play._log_score_anomaly_safe")
@patch("skills.balatro.actions.play.BalatroAlgorithm.estimate_hand_score_for_indices")
@patch("skills.balatro.actions.play.BalatroAlgorithm.auto_optimize_jokers")
def test_play_hand_logs_score_anomaly_when_estimate_drift_exceeds_threshold(
    mock_optimize,
    mock_estimate,
    mock_log_anomaly,
):
    mock_optimize.return_value = {
        "ordered_jokers": [],
        "reorder_indices": [],
        "changed": False,
        "score": 0.0,
    }
    mock_estimate.return_value = 200

    raw_state = {
        "state": "selecting_hand",
        "round": {"chips": 100},
        "hand": {
            "cards": [
                {"value": {"rank": "A", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "K", "suit": "Spades", "effect": ""}},
            ]
        },
        "jokers": {"cards": []},
    }
    ctx = _build_context(raw_state, [1])
    ctx.controller.client.play_hand.return_value = (True, None)

    # Simulate score increase after play to 120 (+20 actual vs 200 estimated).
    def _refresh_state_side_effect():
        ctx.controller.raw_state = {
            **raw_state,
            "round": {"chips": 120},
        }
        return True

    ctx.controller.refresh_state.side_effect = _refresh_state_side_effect
    ctx.tick_context.update_raw_state.side_effect = lambda new_state: setattr(
        ctx.tick_context, "raw_state", new_state
    )

    action = PlayHandAction()
    result = action.execute(ctx)

    assert result.succeeded is True
    mock_log_anomaly.assert_called_once()


@patch("skills.balatro.actions.play._log_score_anomaly_safe")
@patch("skills.balatro.actions.play.BalatroAlgorithm.estimate_hand_score_for_indices")
@patch("skills.balatro.actions.play.BalatroAlgorithm.auto_optimize_jokers")
def test_play_hand_uses_wider_anomaly_threshold_for_rng_jokers(
    mock_optimize,
    mock_estimate,
    mock_log_anomaly,
):
    mock_optimize.return_value = {
        "ordered_jokers": [],
        "reorder_indices": [],
        "changed": False,
        "score": 0.0,
    }
    mock_estimate.return_value = 130

    raw_state = {
        "state": "selecting_hand",
        "round": {"chips": 100},
        "hand": {
            "cards": [
                {"value": {"rank": "A", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "K", "suit": "Spades", "effect": ""}},
            ]
        },
        "jokers": {
            "cards": [
                {
                    "key": "j_bloodstone",
                    "label": "Bloodstone",
                    "value": {"effect": "1 in 2 chance for Hearts to give X2 Mult"},
                }
            ]
        },
    }
    ctx = _build_context(raw_state, [1])
    ctx.controller.client.play_hand.return_value = (True, None)

    # Actual +100 -> +95 (35% drift vs estimate), should be ignored on RNG boards.
    def _refresh_state_side_effect():
        ctx.controller.raw_state = {
            **raw_state,
            "round": {"chips": 195},
        }
        return True

    ctx.controller.refresh_state.side_effect = _refresh_state_side_effect
    ctx.tick_context.update_raw_state.side_effect = lambda new_state: setattr(
        ctx.tick_context, "raw_state", new_state
    )

    action = PlayHandAction()
    result = action.execute(ctx)

    assert result.succeeded is True
    mock_log_anomaly.assert_not_called()


def test_flush_telemetry_is_callable_and_non_blocking():
    from skills.balatro_bot.modules.balatro_telemetry import (
        log_decision,
        flush_telemetry,
    )

    raw_state = {
        "state": "shop",
        "money": 5,
        "ante_num": 1,
        "jokers": {"cards": []},
        "round": {"hands_left": 3, "discards_left": 2},
        "blinds": {"boss": {"name": "", "status": ""}},
    }
    log_decision("continue", True, None, raw_state)
    flush_telemetry()


def test_buy_shop_consumable_slot_full_error_is_contextual():
    raw_state = {
        "state": "shop",
        "money": 30,
        "shop": {
            "cards": [
                {
                    "key": "c_jupiter",
                    "label": "Jupiter",
                    "cost": 3,
                    "value": {"effect": "Level up Flush"},
                }
            ]
        },
        "consumeables": {
            "size": 2,
            "cards": [
                {"key": "c_a", "label": "A", "value": {"effect": ""}},
                {"key": "c_b", "label": "B", "value": {"effect": ""}},
            ],
        },
        "jokers": {"size": 5, "cards": []},
        "hand": {"cards": []},
        "packs": {"cards": []},
        "vouchers": {"cards": []},
    }
    ctx = _build_context(raw_state, [1])
    action = BuyShopAction()
    result = action.execute(ctx)

    assert result.succeeded is False
    assert "Consumable slots full" in (result.error or "")
    assert "use_consumable" in (result.error or "")


def test_buy_shop_boss_survival_gate_blocks_unsafe_pivot():
    raw_state = {
        "state": "shop",
        "money": 50,
        "shop": {
            "cards": [
                {
                    "key": "j_new",
                    "label": "Risky Joker",
                    "cost": 6,
                    "value": {"effect": "X2 Mult"},
                }
            ]
        },
        "blinds": {
            "boss": {
                "name": "The Needle",
                "status": "active",
                "score": 100,
            }
        },
        "jokers": {"size": 5, "cards": []},
        "hand": {
            "cards": [
                {"value": {"rank": "A", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "K", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "Q", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "J", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "9", "suit": "Spades", "effect": ""}},
            ]
        },
        "packs": {"cards": []},
        "vouchers": {"cards": []},
    }
    ctx = _build_context(raw_state, [1])
    ctx.session.run_profile = {"deck": "CHECKERED"}

    with (
        patch(
            "skills.balatro.actions.shop.BalatroAlgorithm.evaluate_shop_joker_impact",
            return_value={
                "projected_score": 90,
                "expected_score": 150,
                "baseline_score": 100,
                "replacement_index": None,
            },
        ),
        patch(
            "skills.balatro.actions.shop._log_survival_gate_blocked_safe"
        ) as mock_gate_log,
    ):
        action = BuyShopAction()
        result = action.execute(ctx)

    assert result.succeeded is False
    assert "survival gate" in (result.error or "").lower()
    mock_gate_log.assert_called_once()


def test_buy_shop_boss_survival_gate_allows_hail_mary_immediate_upgrade():
    raw_state = {
        "state": "shop",
        "money": 50,
        "shop": {
            "cards": [
                {
                    "key": "j_new",
                    "label": "Hail Mary Joker",
                    "cost": 6,
                    "value": {"effect": "X2 Mult"},
                }
            ]
        },
        "blinds": {
            "boss": {
                "name": "The Needle",
                "status": "active",
                "score": 50000,
            }
        },
        "jokers": {"size": 5, "cards": []},
        "hand": {
            "cards": [
                {"value": {"rank": "A", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "K", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "Q", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "J", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "9", "suit": "Spades", "effect": ""}},
            ]
        },
        "packs": {"cards": []},
        "vouchers": {"cards": []},
    }
    ctx = _build_context(raw_state, [1])
    ctx.session.run_profile = {"deck": "CHECKERED"}
    ctx.controller.client.buy.return_value = True
    ctx.controller.refresh_state.return_value = True

    with patch(
        "skills.balatro.actions.shop.BalatroAlgorithm.evaluate_shop_joker_impact",
        return_value={
            "projected_score": 60000,
            "expected_score": 70000,
            "baseline_score": 40000,
            "replacement_index": None,
        },
    ):
        action = BuyShopAction()
        result = action.execute(ctx)

    assert result.succeeded is True


@patch("skills.balatro.actions.shop._summarize_shop_item")
def test_reroll_block_feedback_includes_buy_shop_index_hint(mock_summarize):
    raw_state = {
        "state": "shop",
        "money": 30,
        "round": {"reroll_cost": 1},
        "shop": {
            "cards": [
                {
                    "key": "j_low",
                    "label": "Low Value Joker",
                    "cost": 3,
                    "value": {"effect": "+5 Chips"},
                },
                {
                    "key": "j_scary_face",
                    "label": "Scary Face",
                    "cost": 4,
                    "value": {"effect": "Played face cards give +30 Chips"},
                },
            ]
        },
        "jokers": {"size": 5, "cards": []},
        "hand": {"cards": []},
        "packs": {"cards": []},
        "vouchers": {"cards": []},
    }
    ctx = _build_context(raw_state, [])

    mock_summarize.side_effect = [
        {
            "label": "Low Value Joker",
            "cost": 3,
            "advisor": "LOW VALUE",
            "impact_pct": 0.0,
            "economy_tag": "[SAFE TO BUY]",
        },
        {
            "label": "Scary Face",
            "cost": 4,
            "advisor": "HIGH SYNERGY",
            "impact_pct": 30.0,
            "economy_tag": "[CRITICAL UPGRADE]",
        },
    ]

    action = RerollAction()
    result = action.execute(ctx)

    assert result.succeeded is False
    assert "buy_shop [2]" in (result.error or "")


@patch("skills.balatro.actions.shop._early_game_buy_block_reason", return_value=None)
@patch("skills.balatro.actions.shop._summarize_shop_item")
def test_buy_shop_noncritical_joker_blocked_by_nonrerollable_priority(
    mock_summarize,
    _mock_block_reason,
):
    raw_state = {
        "state": "shop",
        "money": 30,
        "ante_num": 3,
        "shop": {
            "cards": [
                {
                    "key": "j_test_mid",
                    "label": "Mid Joker",
                    "cost": 6,
                    "value": {"effect": "+12 Mult"},
                }
            ]
        },
        "packs": {"cards": []},
        "vouchers": {
            "cards": [
                {
                    "key": "v_overstock_norm",
                    "label": "Overstock",
                    "cost": 10,
                    "value": {"effect": ""},
                }
            ]
        },
        "jokers": {"size": 5, "cards": []},
        "hand": {"cards": []},
    }

    def _summary_for_card(card, *_args, **_kwargs):
        key = str((card or {}).get("key") or "")
        if key.startswith("j_"):
            return {
                "label": "Mid Joker",
                "cost": 6,
                "advisor": "MODERATE VALUE",
                "economy_tag": "[SAFE TO BUY]",
                "impact_pct": 10.0,
                "baseline_score": 0,
                "projected_score": 0,
                "expected_score": 0,
                "immediate_impact": 0,
                "projected_3_round_impact": 0,
                "replace_idx": "",
                "replacement_index": None,
            }
        return {
            "label": "Overstock",
            "cost": 10,
            "advisor": "HIGH SYNERGY",
            "economy_tag": "[SAFE TO BUY]",
            "impact_pct": 0.0,
            "baseline_score": 0,
            "projected_score": 0,
            "expected_score": 0,
            "immediate_impact": 0,
            "projected_3_round_impact": 0,
            "replace_idx": "",
            "replacement_index": None,
        }

    mock_summarize.side_effect = _summary_for_card

    ctx = _build_context(raw_state, [1])
    ctx.session.run_profile = {"deck": "CHECKERED"}

    action = BuyShopAction()
    result = action.execute(ctx)

    assert result.succeeded is False
    assert "Non-rerollable priority rule" in (result.error or "")
    assert "buy_voucher [1]" in (result.error or "")
    ctx.controller.client.buy.assert_not_called()


@patch("skills.balatro.actions.shop._summarize_shop_item")
def test_reroll_blocked_by_nonrerollable_priority(mock_summarize):
    raw_state = {
        "state": "shop",
        "money": 30,
        "round": {"reroll_cost": 1},
        "shop": {"cards": []},
        "packs": {
            "cards": [
                {
                    "key": "p_arcana_normal_1",
                    "label": "Arcana Pack",
                    "cost": 4,
                    "value": {"effect": "Contains Tarot cards"},
                }
            ]
        },
        "vouchers": {"cards": []},
        "jokers": {"size": 5, "cards": []},
        "hand": {"cards": []},
    }

    def _summary_for_card(card, *_args, **_kwargs):
        key = str((card or {}).get("key") or "")
        if key.startswith("p_"):
            return {
                "label": "Arcana Pack",
                "cost": 4,
                "advisor": "HIGH SYNERGY",
                "economy_tag": "[SAFE TO BUY]",
                "impact_pct": 0.0,
                "baseline_score": 0,
                "projected_score": 0,
                "expected_score": 0,
                "immediate_impact": 0,
                "projected_3_round_impact": 0,
                "replace_idx": "",
                "replacement_index": None,
            }
        return {
            "label": "Unknown",
            "cost": 0,
            "advisor": "LOW VALUE",
            "economy_tag": "",
            "impact_pct": 0.0,
            "baseline_score": 0,
            "projected_score": 0,
            "expected_score": 0,
            "immediate_impact": 0,
            "projected_3_round_impact": 0,
            "replace_idx": "",
            "replacement_index": None,
        }

    mock_summarize.side_effect = _summary_for_card

    ctx = _build_context(raw_state, [])

    action = RerollAction()
    result = action.execute(ctx)

    assert result.succeeded is False
    assert "non-rerollable priority" in (result.error or "").lower()
    assert "buy_pack [1]" in (result.error or "")
    ctx.controller.client.reroll.assert_not_called()


@patch("skills.balatro.actions.shop._early_game_buy_block_reason", return_value=None)
@patch("skills.balatro.actions.shop._summarize_shop_item")
def test_buy_shop_noncritical_joker_blocked_by_moderate_value_voucher(
    mock_summarize, _mock_block_reason
):
    """MODERATE VALUE voucher must still gate a non-critical joker buy (Fix 1 scope)."""
    raw_state = {
        "state": "shop",
        "money": 30,
        "ante_num": 3,
        "shop": {
            "cards": [{"key": "j_test", "label": "Mid Joker", "cost": 6,
                        "value": {"effect": "+12 Mult"}}]
        },
        "vouchers": {
            "cards": [{"key": "v_clearance", "label": "Clearance Sale",
                        "cost": 10, "value": {"effect": ""}}]
        },
        "packs": {"cards": []},
        "jokers": {"size": 5, "cards": []},
        "hand": {"cards": []},
    }

    def _summary_for_card(card, *_args, **_kwargs):
        key = str((card or {}).get("key") or "")
        if key.startswith("j_"):
            return {"label": "Mid Joker", "cost": 6, "advisor": "MODERATE VALUE",
                    "economy_tag": "[SAFE TO BUY]", "impact_pct": 10.0,
                    "baseline_score": 0, "projected_score": 0, "expected_score": 0,
                    "immediate_impact": 0, "projected_3_round_impact": 0,
                    "replace_idx": "", "replacement_index": None}
        return {"label": "Clearance Sale", "cost": 10, "advisor": "MODERATE VALUE",
                "economy_tag": "[SAFE TO BUY]", "impact_pct": 0.0,
                "baseline_score": 0, "projected_score": 0, "expected_score": 0,
                "immediate_impact": 0, "projected_3_round_impact": 0,
                "replace_idx": "", "replacement_index": None}

    mock_summarize.side_effect = _summary_for_card
    ctx = _build_context(raw_state, [1])
    ctx.session.run_profile = {"deck": "CHECKERED"}

    action = BuyShopAction()
    result = action.execute(ctx)

    assert result.succeeded is False
    assert "Non-rerollable priority rule" in (result.error or "")
    assert "buy_voucher [1]" in (result.error or "")
    ctx.controller.client.buy.assert_not_called()


# ── Fix 1: LOW VALUE / SITUATIONAL vouchers/packs must NOT gate reroll ──────


@patch("skills.balatro.actions.shop._summarize_shop_item")
def test_reroll_not_blocked_by_low_value_voucher(mock_summarize):
    """A LOW VALUE voucher must no longer trap the reroll loop (Fix 1 regression)."""
    raw_state = {
        "state": "shop",
        "money": 30,
        "round": {"reroll_cost": 1},
        "shop": {"cards": []},
        "packs": {"cards": []},
        "vouchers": {
            "cards": [
                {
                    "key": "v_coupon",
                    "label": "Coupon Book",
                    "cost": 10,
                    "value": {"effect": "First shop item each round is free"},
                }
            ]
        },
        "jokers": {"size": 5, "cards": []},
        "hand": {"cards": []},
    }

    def _summary_for_card(card, *_args, **_kwargs):
        return {
            "label": "Coupon Book",
            "cost": 10,
            "advisor": "LOW VALUE",
            "impact_pct": 0.0,
            "economy_tag": "[COSTS INTEREST]",
            "baseline_score": 0,
            "projected_score": 0,
            "expected_score": 0,
            "immediate_impact": 0,
            "projected_3_round_impact": 0,
            "replace_idx": "",
            "replacement_index": None,
        }

    mock_summarize.side_effect = _summary_for_card
    ctx = _build_context(raw_state, [])
    ctx.controller.client.reroll.return_value = True

    action = RerollAction()
    result = action.execute(ctx)

    # LOW VALUE voucher must NOT block the reroll.
    assert result.succeeded is True, f"Expected reroll to succeed, got: {result.error}"
    ctx.controller.client.reroll.assert_called_once()


@patch("skills.balatro.actions.shop._summarize_shop_item")
def test_reroll_not_blocked_by_situational_pack(mock_summarize):
    """A SITUATIONAL pack must no longer trap the reroll loop (Fix 1 regression)."""
    raw_state = {
        "state": "shop",
        "money": 30,
        "round": {"reroll_cost": 1},
        "shop": {"cards": []},
        "vouchers": {"cards": []},
        "packs": {
            "cards": [
                {
                    "key": "p_spectral_normal_1",
                    "label": "Spectral Pack",
                    "cost": 4,
                    "value": {"effect": "Contains Spectral cards"},
                }
            ]
        },
        "jokers": {"size": 5, "cards": []},
        "hand": {"cards": []},
    }

    def _summary_for_card(card, *_args, **_kwargs):
        return {
            "label": "Spectral Pack",
            "cost": 4,
            "advisor": "SITUATIONAL",
            "impact_pct": 0.0,
            "economy_tag": "",
            "baseline_score": 0,
            "projected_score": 0,
            "expected_score": 0,
            "immediate_impact": 0,
            "projected_3_round_impact": 0,
            "replace_idx": "",
            "replacement_index": None,
        }

    mock_summarize.side_effect = _summary_for_card
    ctx = _build_context(raw_state, [])
    ctx.controller.client.reroll.return_value = True

    action = RerollAction()
    result = action.execute(ctx)

    assert result.succeeded is True, f"Expected reroll to succeed, got: {result.error}"
    ctx.controller.client.reroll.assert_called_once()


@patch("skills.balatro.actions.shop._summarize_shop_item")
def test_reroll_still_blocked_by_moderate_value_pack(mock_summarize):
    """MODERATE VALUE pack must still block reroll — it's considered worth buying first."""
    raw_state = {
        "state": "shop",
        "money": 30,
        "round": {"reroll_cost": 1},
        "shop": {"cards": []},
        "vouchers": {"cards": []},
        "packs": {
            "cards": [
                {
                    "key": "p_arcana_normal_1",
                    "label": "Arcana Pack",
                    "cost": 4,
                    "value": {"effect": "Contains Tarot cards"},
                }
            ]
        },
        "jokers": {"size": 5, "cards": []},
        "hand": {"cards": []},
    }

    def _summary_for_card(card, *_args, **_kwargs):
        return {
            "label": "Arcana Pack",
            "cost": 4,
            "advisor": "MODERATE VALUE",
            "impact_pct": 5.0,
            "economy_tag": "[SAFE TO BUY]",
            "baseline_score": 0,
            "projected_score": 0,
            "expected_score": 0,
            "immediate_impact": 0,
            "projected_3_round_impact": 0,
            "replace_idx": "",
            "replacement_index": None,
        }

    mock_summarize.side_effect = _summary_for_card
    ctx = _build_context(raw_state, [])

    action = RerollAction()
    result = action.execute(ctx)

    assert result.succeeded is False
    assert "non-rerollable priority" in (result.error or "").lower()
    assert "buy_pack [1]" in (result.error or "")
    ctx.controller.client.reroll.assert_not_called()



# -- Fix 2: Phase-aware reserve -- _get_reserve_for_ante unit tests -----------


def test_get_reserve_for_ante_thresholds():
    """Direct unit test: correct reserve returned for each ante tier."""
    from skills.balatro.shop_analysis import _get_reserve_for_ante

    assert _get_reserve_for_ante(1, 50) == 25, "Ante 1 should use early-game reserve"
    assert _get_reserve_for_ante(2, 50) == 25, "Ante 2 should use early-game reserve"
    assert _get_reserve_for_ante(3, 50) == 10, "Ante 3 should use mid-game reserve"
    assert _get_reserve_for_ante(4, 50) == 10, "Ante 4 should use mid-game reserve"
    assert _get_reserve_for_ante(5, 50) == 5,  "Ante 5 should use late-game reserve"
    assert _get_reserve_for_ante(8, 99) == 5,  "Ante 8 should use late-game reserve"


# -- Fix 2: Reroll reserve gate integrates phase-aware reserve ----------------


def _make_reroll_state(
    ante: int,
    money: int,
    reroll_cost: int = 1,
    joker_cards: list | None = None,
) -> dict:
    """Helper: minimal raw_state for RerollAction tests at a given ante.

    Pass ``joker_cards`` to simulate an established board. When omitted the
    lineup is empty, which triggers the survival exception.  For tests that
    specifically probe the reserve gate the caller should inject scoring jokers
    so that ``weak_synergy`` evaluates to False and the exception is suppressed.
    """
    return {
        "state": "shop",
        "money": money,
        "ante_num": ante,
        "round": {"reroll_cost": reroll_cost},
        "shop": {"cards": []},
        "packs": {"cards": []},
        "vouchers": {"cards": []},
        "jokers": {"size": 5, "cards": joker_cards or []},
        "hand": {"cards": []},
    }


def test_reroll_reserve_gate_ante2_blocks_when_below_25():
    """Ante 2: reserve is $25 -- reroll leaving post=$24 is blocked by reserve gate.

    post_reroll=$24 clears CRITICAL_SPEND_FLOOR=$15 but falls below reserve=$25.
    3 scoring jokers are injected so weak_synergy=False, suppressing the survival
    exception that would otherwise allow the dip.
    """
    _scoring_jokers = [
        {"key": "j_a", "label": "A", "value": {"effect": "+20 Mult"}},
        {"key": "j_b", "label": "B", "value": {"effect": "+20 Mult"}},
        {"key": "j_c", "label": "C", "value": {"effect": "+20 Mult"}},
    ]
    raw_state = _make_reroll_state(ante=2, money=25, reroll_cost=1, joker_cards=_scoring_jokers)
    ctx = _build_context(raw_state, [])

    action = RerollAction()
    result = action.execute(ctx)

    assert result.succeeded is False
    assert "reserve rule" in (result.error or "").lower(), (
        f"Expected reserve-rule block at Ante 2 with post=$24, got: {result.error}"
    )
    ctx.controller.client.reroll.assert_not_called()

def test_reroll_reserve_gate_ante3_allows_when_above_10():
    """Ante 3: reserve drops to $10 -- reroll leaving post=$16 must be allowed.

    post_reroll=$16 clears both CRITICAL_SPEND_FLOOR=$15 and reserve=$10.
    """
    raw_state = _make_reroll_state(ante=3, money=17, reroll_cost=1)
    ctx = _build_context(raw_state, [])
    ctx.controller.client.reroll.return_value = True

    action = RerollAction()
    result = action.execute(ctx)

    assert result.succeeded is True, (
        f"Expected reroll to succeed at Ante 3 with $17 (reserve=$10), got: {result.error}"
    )
    ctx.controller.client.reroll.assert_called_once()


def test_reroll_reserve_gate_ante6_allows_when_above_5():
    """Ante 6: reserve drops to $5 -- reroll leaving post=$16 must be allowed.

    post_reroll=$16 clears both CRITICAL_SPEND_FLOOR=$15 and reserve=$5.
    """
    raw_state = _make_reroll_state(ante=6, money=17, reroll_cost=1)
    ctx = _build_context(raw_state, [])
    ctx.controller.client.reroll.return_value = True

    action = RerollAction()
    result = action.execute(ctx)

    assert result.succeeded is True, (
        f"Expected reroll to succeed at Ante 6 with $17 (reserve=$5), got: {result.error}"
    )
    ctx.controller.client.reroll.assert_called_once()


# -- Fix 3

@patch("skills.balatro.actions.shop.BalatroAlgorithm.identify_safe_sell_jokers")
def test_slot_full_error_does_not_suggest_high_impact_joker_as_sell(mock_safe_sells):
    """Part A: tolerance_pct=5.0 means a 10%-impact joker must NOT appear in the sell hint."""
    mock_safe_sells.return_value = []  # nothing qualifies at 5% tolerance

    raw_state = {
        "state": "shop",
        "money": 30,
        "shop": {
            "cards": [
                {
                    "key": "j_new",
                    "label": "New Joker",
                    "cost": 6,
                    "value": {"effect": "+20 Mult"},
                }
            ]
        },
        "jokers": {
            "size": 5,
            "cards": [
                {"key": "j_a", "label": "A", "value": {"effect": "+10 Mult"}},
                {"key": "j_b", "label": "B", "value": {"effect": "+10 Mult"}},
                {"key": "j_c", "label": "C", "value": {"effect": "+10 Mult"}},
                {"key": "j_d", "label": "D", "value": {"effect": "+10 Mult"}},
                {"key": "j_e", "label": "E", "value": {"effect": "+10 Mult"}},
            ],
        },
        "hand": {"cards": []},
        "packs": {"cards": []},
        "vouchers": {"cards": []},
    }
    ctx = _build_context(raw_state, [1])
    action = BuyShopAction()
    result = action.execute(ctx)

    assert result.succeeded is False
    assert "Joker slots full" in (result.error or "")
    # No safe sell candidate — hint must not name any joker
    assert "Math suggests selling" not in (result.error or "")
    kwargs = mock_safe_sells.call_args.kwargs
    assert kwargs.get("tolerance_pct") == 5.0


def test_shop_strategy_block_emits_hold_when_improvement_below_threshold():
    """Part B: replace_idx present but impact_pct=5% → HOLD, not RECOMMENDED."""
    from skills.balatro.shop_analysis import _build_shop_strategy_block

    raw_state = {
        "state": "shop",
        "money": 30,
        "ante_num": 4,
        "round": {"reroll_cost": 1},
        "shop": {
            "cards": [
                {
                    "key": "j_weak",
                    "label": "Weak Joker",
                    "cost": 5,
                    "value": {"effect": "+8 Mult"},
                }
            ]
        },
        "jokers": {
            "size": 5,
            "cards": [
                {"key": "j_a", "label": "A", "value": {"effect": "+10 Mult"}},
                {"key": "j_b", "label": "B", "value": {"effect": "+10 Mult"}},
                {"key": "j_c", "label": "C", "value": {"effect": "+10 Mult"}},
                {"key": "j_d", "label": "D", "value": {"effect": "+10 Mult"}},
                {"key": "j_e", "label": "E", "value": {"effect": "+10 Mult"}},
            ],
        },
        "hand": {"cards": []},
        "packs": {"cards": []},
        "vouchers": {"cards": []},
    }

    with (
        patch(
            "skills.balatro.shop_analysis.BalatroAlgorithm.identify_safe_sell_jokers",
            return_value=[
                {
                    "index": 1,
                    "loss_pct": 1.0,
                    "combined_loss_abs": 10,
                    "reason": "LOW IMPACT",
                }
            ],
        ),
        patch(
            "skills.balatro.shop_analysis._summarize_shop_item",
            return_value={
                "label": "Weak Joker",
                "cost": 5,
                "advisor": "LOW VALUE",
                "economy_tag": "[SAFE TO BUY]",
                "impact_pct": 5.0,
                "replace_idx": "1",
                "replacement_index": 1,
                "baseline_score": 100,
                "projected_score": 105,
                "expected_score": 110,
                "immediate_impact": 5,
                "projected_3_round_impact": 10,
            },
        ),
    ):
        block = _build_shop_strategy_block(raw_state, hand_cards=[])

    assert "HOLD: 5.0% improvement does not justify selling" in block
    assert "RECOMMENDED: sell_joker" not in block


def test_shop_strategy_block_emits_recommended_when_improvement_meets_threshold():
    """Part B: replace_idx present and impact_pct=20% → RECOMMENDED."""
    from skills.balatro.shop_analysis import _build_shop_strategy_block

    raw_state = {
        "state": "shop",
        "money": 30,
        "ante_num": 4,
        "round": {"reroll_cost": 1},
        "shop": {
            "cards": [
                {
                    "key": "j_strong",
                    "label": "Strong Joker",
                    "cost": 5,
                    "value": {"effect": "X2 Mult"},
                }
            ]
        },
        "jokers": {
            "size": 5,
            "cards": [
                {"key": "j_a", "label": "A", "value": {"effect": "+10 Mult"}},
                {"key": "j_b", "label": "B", "value": {"effect": "+10 Mult"}},
                {"key": "j_c", "label": "C", "value": {"effect": "+10 Mult"}},
                {"key": "j_d", "label": "D", "value": {"effect": "+10 Mult"}},
                {"key": "j_e", "label": "E", "value": {"effect": "+10 Mult"}},
            ],
        },
        "hand": {"cards": []},
        "packs": {"cards": []},
        "vouchers": {"cards": []},
    }

    with (
        patch(
            "skills.balatro.shop_analysis.BalatroAlgorithm.identify_safe_sell_jokers",
            return_value=[
                {
                    "index": 1,
                    "loss_pct": 1.0,
                    "combined_loss_abs": 10,
                    "reason": "LOW IMPACT",
                }
            ],
        ),
        patch(
            "skills.balatro.shop_analysis._summarize_shop_item",
            return_value={
                "label": "Strong Joker",
                "cost": 5,
                "advisor": "HIGH SYNERGY",
                "economy_tag": "[SAFE TO BUY]",
                "impact_pct": 20.0,
                "replace_idx": "1",
                "replacement_index": 1,
                "baseline_score": 100,
                "projected_score": 120,
                "expected_score": 130,
                "immediate_impact": 20,
                "projected_3_round_impact": 30,
            },
        ),
    ):
        block = _build_shop_strategy_block(raw_state, hand_cards=[])

    assert "RECOMMENDED: sell_joker [1] then buy_shop [1]" in block
    assert "HOLD" not in block


# ---------------------------------------------------------------------------
# Fix 6 — BLIND_SAFETY_MARGIN_BY_ANTE tests
# ---------------------------------------------------------------------------

def _make_survival_gate_state(
    ante: int,
    blind_score: int,
    projected_score: int,
    baseline_score: int,
    *,
    boss_active: bool = True,
) -> dict:
    """Helper: build a minimal raw_state for survival-gate tests."""
    return {
        "state": "shop",
        "money": 50,
        "ante_num": ante,
        "shop": {
            "cards": [
                {
                    "key": "j_xmult",
                    "label": "Multiplier",
                    "cost": 6,
                    "value": {"effect": "X3 Mult"},
                }
            ]
        },
        "blinds": {
            "boss": {
                "name": "The Needle",
                "status": "active" if boss_active else "complete",
                "score": blind_score,
            }
        },
        "jokers": {"size": 5, "cards": []},
        "hand": {
            "cards": [
                {"value": {"rank": "A", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "K", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "Q", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "J", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "9", "suit": "Spades", "effect": ""}},
            ]
        },
        "packs": {"cards": []},
        "vouchers": {"cards": []},
        "_projected_score": projected_score,
        "_baseline_score": baseline_score,
    }


def test_blind_safety_margin_by_ante_constant_values():
    """BLIND_SAFETY_MARGIN_BY_ANTE has the exact values mandated by the plan."""
    from skills.balatro.actions.shop import BLIND_SAFETY_MARGIN_BY_ANTE

    expected = {1: 1.5, 2: 1.5, 3: 1.3, 4: 1.2, 5: 1.0, 6: 0.9, 7: 0.85, 8: 0.8}
    assert BLIND_SAFETY_MARGIN_BY_ANTE == expected, (
        f"Constant mismatch: {BLIND_SAFETY_MARGIN_BY_ANTE!r}"
    )


def test_survival_gate_ante2_tight_blocks_pivot():
    """
    Ante 2 margin = 1.5 → gated_requirement = 100 * 1.5 * 1.5 = 225.
    projected_score = 200 < 225, is_immediate_upgrade False → gate must block.
    """
    raw_state = _make_survival_gate_state(
        ante=2,
        blind_score=100,
        projected_score=200,
        baseline_score=200,  # not a strict upgrade (equal)
    )
    ctx = _build_context(raw_state, [1])
    ctx.session.run_profile = {"deck": "CHECKERED"}

    with (
        patch(
            "skills.balatro.actions.shop.BalatroAlgorithm.evaluate_shop_joker_impact",
            return_value={
                "projected_score": 200,
                "expected_score": 250,
                "baseline_score": 200,
                "replacement_index": None,
            },
        ),
        patch("skills.balatro.actions.shop._log_survival_gate_blocked_safe"),
    ):
        result = BuyShopAction().execute(ctx)

    assert result.succeeded is False, "Gate should block at Ante 2 (margin=1.5)"
    assert "survival gate" in (result.error or "").lower()


def test_survival_gate_ante6_relaxed_allows_pivot():
    """
    Ante 6 margin = 0.9 → gated_requirement = 100 * 1.5 * 0.9 = 135.
    projected_score = 140 ≥ 135 → gate must allow the pivot.
    """
    raw_state = _make_survival_gate_state(
        ante=6,
        blind_score=100,
        projected_score=140,
        baseline_score=90,
    )
    ctx = _build_context(raw_state, [1])
    ctx.session.run_profile = {"deck": "CHECKERED"}
    ctx.controller.client.buy.return_value = True
    ctx.controller.refresh_state.return_value = True

    with patch(
        "skills.balatro.actions.shop.BalatroAlgorithm.evaluate_shop_joker_impact",
        return_value={
            "projected_score": 140,
            "expected_score": 200,
            "baseline_score": 90,
            "replacement_index": None,
        },
    ):
        result = BuyShopAction().execute(ctx)

    assert result.succeeded is True, (
        f"Gate should allow at Ante 6 (margin=0.9) but got: {result.error!r}"
    )


def test_survival_gate_ante7_allows_pivot_that_ante2_would_block():
    """
    projected_score == baseline_score (400) so is_strict_immediate_upgrade is always False
    and the gate path is always exercised.  blind_score=300, boss active (boss_mult=1.5):
      • Ante 2: gated = 300 * 1.5 * 1.5 = 675  → 400 < 675  → BLOCKED.
      • Ante 7: gated = 300 * 1.5 * 0.85 = 382  → 400 ≥ 382  → ALLOWED.
    This proves the margin meaningfully relaxes the gate at higher antes.
    """
    # projected_score == baseline_score → is_strict_immediate_upgrade is *never* True,
    # so the gate comparison is the only path that determines the outcome.
    shared_summary = {
        "label": "Multiplier",
        "cost": 6,
        "advisor": "HIGH SYNERGY",
        "economy_tag": "[SAFE TO BUY]",
        "impact_pct": 0.0,
        "replace_idx": "",
        "replacement_index": None,
        "baseline_score": 400,
        "projected_score": 400,   # equal → no strict-upgrade bypass
        "expected_score": 600,
        "immediate_impact": 0,
        "projected_3_round_impact": 200,
    }

    # ── Ante 2: expect blocked (gated_requirement = 675 > 400) ──
    state_ante2 = _make_survival_gate_state(
        ante=2, blind_score=300, projected_score=400, baseline_score=400
    )
    ctx2 = _build_context(state_ante2, [1])
    ctx2.session.run_profile = {"deck": "RED"}

    with (
        patch("skills.balatro.actions.shop._summarize_shop_item", return_value=shared_summary),
        patch("skills.balatro.actions.shop._early_game_buy_block_reason", return_value=None),
        patch("skills.balatro.actions.shop._log_survival_gate_blocked_safe"),
    ):
        result_ante2 = BuyShopAction().execute(ctx2)

    assert result_ante2.succeeded is False, (
        "Ante 2 should block: gated_requirement=675 > projected=400"
    )
    assert "survival gate" in (result_ante2.error or "").lower()

    # ── Ante 7: same numbers, expect allowed (gated_requirement = 382 ≤ 400) ──
    state_ante7 = _make_survival_gate_state(
        ante=7, blind_score=300, projected_score=400, baseline_score=400
    )
    ctx7 = _build_context(state_ante7, [1])
    ctx7.session.run_profile = {"deck": "RED"}
    ctx7.controller.client.buy.return_value = True
    ctx7.controller.refresh_state.return_value = True

    with (
        patch("skills.balatro.actions.shop._summarize_shop_item", return_value=shared_summary),
        patch("skills.balatro.actions.shop._early_game_buy_block_reason", return_value=None),
    ):
        result_ante7 = BuyShopAction().execute(ctx7)

    assert result_ante7.succeeded is True, (
        f"Ante 7 should allow: gated_requirement=382 ≤ projected=400; got: {result_ante7.error!r}"
    )



# ---------------------------------------------------------------------------
# Fix 1 -- Dynamic reroll floor respects phase-aware reserve_target
# ---------------------------------------------------------------------------


def test_reroll_floor_uses_reserve_target_ante3():
    """Ante 3: reserve=$10, absolute_floor=min(15,10)=10.

    money=$12, reroll_cost=$1 -> post=$11 >= $10 -> reroll must be ALLOWED.
    Before the fix this was blocked because post=$11 < CRITICAL_SPEND_FLOOR=$15.
    """
    raw_state = {
        "state": "shop",
        "money": 12,
        "ante_num": 3,
        "round": {"reroll_cost": 1},
        "shop": {"cards": []},
        "packs": {"cards": []},
        "vouchers": {"cards": []},
        "jokers": {"size": 5, "cards": []},
        "hand": {"cards": []},
    }
    ctx = _build_context(raw_state, [])
    ctx.controller.client.reroll.return_value = True

    result = RerollAction().execute(ctx)

    assert result.succeeded is True, (
        f"Ante 3, post=$11, floor=min(15,10)=10: reroll should succeed, got: {result.error!r}"
    )
    ctx.controller.client.reroll.assert_called_once()


def test_reroll_floor_uses_reserve_target_ante5():
    """Ante 5: reserve=$5, absolute_floor=min(15,5)=5.

    money=$7, reroll_cost=$1 -> post=$6 >= $5 -> reroll must be ALLOWED.
    Before the fix this was blocked because post=$6 < CRITICAL_SPEND_FLOOR=$15.
    """
    raw_state = {
        "state": "shop",
        "money": 7,
        "ante_num": 5,
        "round": {"reroll_cost": 1},
        "shop": {"cards": []},
        "packs": {"cards": []},
        "vouchers": {"cards": []},
        "jokers": {"size": 5, "cards": []},
        "hand": {"cards": []},
    }
    ctx = _build_context(raw_state, [])
    ctx.controller.client.reroll.return_value = True

    result = RerollAction().execute(ctx)

    assert result.succeeded is True, (
        f"Ante 5, post=$6, floor=min(15,5)=5: reroll should succeed, got: {result.error!r}"
    )
    ctx.controller.client.reroll.assert_called_once()


# ---------------------------------------------------------------------------
# Fix 2 -- Voluntary sell gate: blocked when slots not full
# ---------------------------------------------------------------------------


def test_sell_joker_blocked_when_slots_not_full():
    """SellJokerAction must be blocked when joker_slots_used < joker_slots_total.

    3 jokers in a 5-slot board -> gate fires before identify_safe_sell_jokers is
    consulted. Error must name the joker and cite the slot counts.
    """
    raw_state = {
        "state": "shop",
        "money": 10,
        "ante_num": 3,
        "jokers": {
            "size": 5,
            "cards": [
                {"key": "j_a", "label": "Mystic Summit", "value": {"effect": "+15 Mult"}, "cost": {"sell": 3}},
                {"key": "j_b", "label": "Joker B", "value": {"effect": "+5 Chips"}, "cost": {"sell": 2}},
                {"key": "j_c", "label": "Joker C", "value": {"effect": "+5 Chips"}, "cost": {"sell": 2}},
            ],
        },
        "shop": {"cards": []},
        "hand": {"cards": []},
        "packs": {"cards": []},
        "vouchers": {"cards": []},
    }
    ctx = _build_context(raw_state, [1])

    result = SellJokerAction().execute(ctx)

    assert result.succeeded is False
    assert "slots are not full" in (result.error or "").lower(), (
        f"Expected slot-gate block, got: {result.error!r}"
    )
    assert "3/5" in (result.error or ""), (
        f"Expected slot counts in error, got: {result.error!r}"
    )
    ctx.controller.client.sell.assert_not_called()


@patch("skills.balatro.actions.utility.BalatroAlgorithm.identify_safe_sell_jokers")
def test_sell_joker_allowed_when_slots_full(mock_safe_sells):
    """SellJokerAction must pass through when all joker slots are occupied
    and the target joker is identified as safe to sell.

    identify_safe_sell_jokers is mocked to return joker #1 as safe so the
    safe-sell gate does not mask whether the slot-capacity check correctly
    allows the action when slots are full (5/5).
    """
    mock_safe_sells.return_value = [
        {"index": 1, "loss_pct": 0.0, "combined_loss_abs": 0, "reason": "LOW IMPACT"}
    ]

    raw_state = {
        "state": "shop",
        "money": 10,
        "ante_num": 4,
        "jokers": {
            "size": 5,
            "cards": [
                {"key": "j_a", "label": "Weak A", "value": {"effect": "+2 Mult"}, "cost": {"sell": 2}},
                {"key": "j_b", "label": "Joker B", "value": {"effect": "+5 Chips"}, "cost": {"sell": 2}},
                {"key": "j_c", "label": "Joker C", "value": {"effect": "+5 Chips"}, "cost": {"sell": 2}},
                {"key": "j_d", "label": "Joker D", "value": {"effect": "+5 Chips"}, "cost": {"sell": 2}},
                {"key": "j_e", "label": "Joker E", "value": {"effect": "+5 Chips"}, "cost": {"sell": 2}},
            ],
        },
        "shop": {"cards": []},
        "hand": {"cards": []},
        "packs": {"cards": []},
        "vouchers": {"cards": []},
    }
    ctx = _build_context(raw_state, [1])
    ctx.controller.client.sell.return_value = True

    result = SellJokerAction().execute(ctx)

    assert result.succeeded is True, (
        f"Slots full (5/5) and joker is safe: sell should succeed, got: {result.error!r}"
    )
    ctx.controller.client.sell.assert_called_once()
