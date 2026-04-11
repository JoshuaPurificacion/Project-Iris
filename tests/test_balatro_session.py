import pytest
import threading
import time
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

from skills.balatro.session import (
    BalatroSession,
    TickContext,
    PlannerOutput,
    _call_planner,
    _sanitize_planner_action,
    start_session,
    stop_session,
    get_active_session,
)
from skills.balatro.shop_analysis import _summarize_shop_item, _format_shop_item_summary


@pytest.fixture
def mock_iris():
    iris = MagicMock()
    iris.vm = None
    iris.messages = []
    # Mock lock for prompt injection
    iris.lock = MagicMock()
    iris.lock.__enter__ = MagicMock(return_value=None)
    iris.lock.__exit__ = MagicMock(return_value=None)
    return iris


def test_tick_context_immutability():
    """Ensure TickContext properly deep-copies the raw state to prevent mutation leaks."""
    original_state = {
        "ante_num": 1,
        "hand": {"cards": [{"suit": "Spades", "value": "10"}], "count": 1},
        "jokers": {"cards": []},
    }

    context = TickContext.create(original_state)

    # Mutate the original state directly
    original_state["ante_num"] = 2
    original_state["hand"]["cards"][0]["suit"] = "Hearts"

    # Assert context state is unharmed
    assert context.raw_state["ante_num"] == 1
    assert context.raw_state["hand"]["cards"][0]["suit"] == "Spades"


@patch("skills.balatro.session.GameController")
def test_session_lifecycle_start_and_stop(mock_controller, mock_iris):
    """Test start and stop flow updates active_session globally."""
    stop_session()  # Clear any existing tests
    assert get_active_session() is None

    start_session(mock_iris, avatar=None)
    session1 = get_active_session()

    assert session1 is not None
    assert isinstance(session1, BalatroSession)
    assert not session1.stop_event.is_set()
    mock_iris.switch_mode.assert_called_with("balatro", avatar=None)

    stop_session()
    assert get_active_session() is None
    assert session1.stop_event.is_set()


@patch("skills.balatro.session.GameController")
def test_session_active_replacement(mock_controller, mock_iris):
    """Verify that starting a new session instantly terminates and replaces an old one."""
    stop_session()

    start_session(mock_iris, avatar=None)
    old_session = get_active_session()

    # Call start again
    start_session(mock_iris, avatar=None)
    new_session = get_active_session()

    assert new_session is not None
    assert old_session is not new_session
    assert old_session.stop_event.is_set(), "Old session must be stopped when replaced"
    assert not new_session.stop_event.is_set(), "New session should be active"

    stop_session()


@patch("skills.balatro.session.GameController")
def test_session_isolation(mock_controller, mock_iris):
    """Verify state data like memory and profiles are fully isolated between sessions."""
    s1 = BalatroSession(mock_iris, None)
    s2 = BalatroSession(mock_iris, None)

    s1.run_profile["deck"] = "CHECKERED"
    s1.action_memory.append("test action 1")

    assert s2.run_profile == {}, "Run profile leaked between sessions."
    assert len(s2.action_memory) == 0, "Action memory leaked between sessions."


def test_planner_output_accepts_synergy_evaluation_field():
    payload = {
        "synergy_evaluation": "Scary Face adds face-card chips, Wily adds 3oak chips; still needs mult scaling.",
        "action": "buy_shop",
        "indices": [1],
        "reasoning": "Positive chip upgrade and affordable.",
    }

    parsed = PlannerOutput.model_validate(payload)

    assert parsed.synergy_evaluation.startswith("Scary Face")
    assert parsed.action == "buy_shop"
    assert parsed.indices == [1]


def test_planner_output_keeps_backward_compat_when_synergy_missing():
    payload = {
        "action": "continue",
        "indices": [],
        "reasoning": "No valid buy.",
    }

    parsed = PlannerOutput.model_validate(payload)

    assert parsed.synergy_evaluation == ""
    assert parsed.action == "continue"


def test_planner_output_maps_shop_index_alias_to_indices():
    parsed = PlannerOutput.model_validate(
        {
            "synergy_evaluation": "",
            "action": "buy_shop",
            "shop_index": 2,
            "reasoning": "Top value buy",
        }
    )

    assert parsed.action == "buy_shop"
    assert parsed.indices == [2]


def test_planner_output_does_not_override_explicit_indices_with_alias():
    parsed = PlannerOutput.model_validate(
        {
            "synergy_evaluation": "",
            "action": "buy_shop",
            "indices": [1],
            "shop_index": 3,
            "reasoning": "Keep explicit indices",
        }
    )

    assert parsed.indices == [1]


def test_planner_output_coerces_numeric_string_indices():
    parsed = PlannerOutput.model_validate(
        {
            "synergy_evaluation": "",
            "action": "buy_shop",
            "indices": ["2"],
            "reasoning": "numeric string",
        }
    )

    assert parsed.indices == [2]


def test_planner_output_normalizes_action_aliases_at_parse_boundary():
    parsed = PlannerOutput.model_validate(
        {
            "synergy_evaluation": "",
            "action": "cash out",
            "indices": [],
            "reasoning": "alias",
        }
    )
    assert parsed.action == "cash_out"


def test_sanitize_planner_action_is_policy_only_not_lexical():
    parsed, reason, meta = _sanitize_planner_action(
        {"action": "cash out", "indices": [], "reasoning": "raw"},
        allowed_actions={"cash_out", "continue"},
        phase_id="round_eval",
        raw_action="cash out",
    )

    assert parsed["action"] == "continue"
    assert reason is not None
    assert "unknown" in reason.lower()
    assert meta["raw_action"] == "cash out"
    assert meta["normalized_action"] == "cash out"


@patch("skills.balatro.session.GameController")
def test_state_action_blocks_clear_when_state_hash_changes(mock_controller, mock_iris):
    session = BalatroSession(mock_iris, None)
    session._blocked_actions_phase = "playing_hand"
    session._blocked_actions_for_state["sell_joker"] = "blocked"

    session._reset_state_action_blocks("shop")

    assert session._blocked_actions_phase == "shop"
    assert session._blocked_actions_for_state == {}


@patch("skills.balatro.session.GameController")
def test_state_action_blocks_persist_within_same_phase(mock_controller, mock_iris):
    """Blocks must NOT clear when the state hash resets but the phase name stays the same."""
    session = BalatroSession(mock_iris, None)
    session._blocked_actions_phase = "playing_hand"
    session._blocked_actions_for_state["buy_shop"] = "not allowed in play phase"

    # Simulate the last_state_hash being forced to None (forced re-plan) while
    # the actual game phase hasn't changed.
    session._reset_state_action_blocks("playing_hand")

    assert session._blocked_actions_phase == "playing_hand"
    assert "buy_shop" in session._blocked_actions_for_state, (
        "Blocked actions must persist when the phase name has not changed"
    )


@patch("skills.balatro.session.GameController")
def test_state_aware_recovery_round_eval_uses_cash_out_not_next_round(
    mock_controller, mock_iris
):
    session = BalatroSession(mock_iris, None)

    controller = MagicMock()
    controller.client._call.return_value = {"ok": True}

    recovered, message = session._attempt_state_aware_recovery(
        controller, {"state": "round_eval"}
    )

    assert recovered is True
    assert "cash_out" in message
    controller.client._call.assert_called_with("cash_out")
    controller.client.proceed_next.assert_not_called()


@patch("skills.balatro.session.GameController")
def test_state_aware_recovery_shop_hard_exits_without_reroll(
    mock_controller, mock_iris
):
    session = BalatroSession(mock_iris, None)

    controller = MagicMock()
    controller.client.proceed_next.return_value = True

    recovered, message = session._attempt_state_aware_recovery(
        controller, {"state": "shop"}
    )

    assert recovered is True
    assert "hard-exit" in message.lower()
    controller.client.proceed_next.assert_called_once()
    controller.client.reroll.assert_not_called()


@patch("skills.balatro.session.GameController")
def test_state_aware_recovery_play_hand_forces_replan(mock_controller, mock_iris):
    session = BalatroSession(mock_iris, None)

    controller = MagicMock()
    controller.refresh_state.return_value = True

    recovered, message = session._attempt_state_aware_recovery(
        controller, {"state": "play_hand"}
    )

    assert recovered is True
    assert "planner re-evaluation" in message.lower()
    controller.refresh_state.assert_called_once()
    controller.client.proceed_next.assert_not_called()


def test_shop_economy_tags_cover_interest_boundary_and_consumable_exception():
    raw_state = {
        "state": "shop",
        "money": 26,
        "ante_num": 3,
        "hand": {
            "cards": [
                {"value": {"rank": "A", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "K", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "Q", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "J", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "9", "suit": "Spades", "effect": ""}},
            ]
        },
        "jokers": {"size": 5, "cards": []},
        "shop": {"cards": []},
        "packs": {"cards": []},
        "vouchers": {"cards": []},
    }

    low_priority_item = {
        "key": "p_standard",
        "label": "Standard Pack",
        "cost": 2,
        "value": {"effect": ""},
    }
    low_summary = _summarize_shop_item(low_priority_item, raw_state, "CHECKERED")
    low_rendered = _format_shop_item_summary(low_summary)
    assert low_summary.get("economy_tag") == "[COSTS INTEREST]"
    assert "economy=[COSTS INTEREST]" in low_rendered

    exact_boundary_item = {
        "key": "p_standard",
        "label": "Standard Pack",
        "cost": 1,
        "value": {"effect": ""},
    }
    exact_boundary_summary = _summarize_shop_item(
        exact_boundary_item, raw_state, "CHECKERED"
    )
    exact_boundary_rendered = _format_shop_item_summary(exact_boundary_summary)
    assert exact_boundary_summary.get("economy_tag") == "[SAFE TO BUY]"
    assert "economy=[SAFE TO BUY]" in exact_boundary_rendered

    tarot_item = {
        "key": "c_tarot_hermit",
        "label": "Hermit",
        "cost": 5,
        "value": {"effect": ""},
    }
    tarot_summary = _summarize_shop_item(tarot_item, raw_state, "CHECKERED")
    tarot_rendered = _format_shop_item_summary(tarot_summary)
    assert tarot_summary.get("economy_tag") == "[CRITICAL UPGRADE]"
    assert "economy=[CRITICAL UPGRADE]" in tarot_rendered


def test_shop_summary_exposes_clean_immediate_and_projected_numbers():
    raw_state = {
        "state": "shop",
        "money": 20,
        "ante_num": 2,
        "hand": {
            "cards": [
                {"value": {"rank": "A", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "K", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "Q", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "J", "suit": "Spades", "effect": ""}},
                {"value": {"rank": "9", "suit": "Hearts", "effect": ""}},
            ]
        },
        "jokers": {"size": 5, "cards": []},
        "shop": {"cards": []},
        "packs": {"cards": []},
        "vouchers": {"cards": []},
    }
    joker_item = {
        "key": "j_scaler",
        "label": "Spade Scaler",
        "cost": 6,
        "value": {"effect": "Gains +12 Mult per Spade played"},
    }

    summary = _summarize_shop_item(joker_item, raw_state, "CHECKERED")

    assert "immediate_impact" in summary
    assert "projected_3_round_impact" in summary
    assert isinstance(summary["immediate_impact"], int)
    assert isinstance(summary["projected_3_round_impact"], int)


@patch("skills.balatro.session.ollama.chat")
def test_call_planner_falls_back_when_action_is_json_field_name(mock_chat):
    mock_chat.return_value = {
        "message": {
            "content": '{"synergy_evaluation":"","action":"indices","indices":[1],"reasoning":"bad action token"}'
        }
    }

    parsed = _call_planner(
        "test-context",
        allowed_actions={"continue", "buy_shop"},
        phase_id="shop",
    )

    assert parsed["action"] == "continue"
    assert parsed["indices"] == []
    meta = parsed.get("_planner_telemetry")
    assert isinstance(meta, dict)
    assert meta.get("phase_id") == "shop"
    assert meta.get("raw_action") == "indices"
    assert meta.get("normalized_action") == "indices"
    assert meta.get("fallback_applied") is True


@patch("skills.balatro.session.ollama.chat")
def test_call_planner_rejects_action_not_allowed_in_phase(mock_chat):
    mock_chat.return_value = {
        "message": {
            "content": '{"synergy_evaluation":"","action":"buy_shop","indices":[1],"reasoning":"buy now"}'
        }
    }

    parsed = _call_planner(
        "test-context",
        allowed_actions={"continue", "play_hand"},
        phase_id="play_discard",
    )

    assert parsed["action"] == "continue"
    assert parsed["indices"] == []
    meta = parsed.get("_planner_telemetry")
    assert isinstance(meta, dict)
    assert meta.get("phase_id") == "play_discard"
    assert meta.get("raw_action") == "buy_shop"
    assert meta.get("normalized_action") == "buy_shop"
    assert "continue" in meta.get("allowed_actions", [])
    assert "play_hand" in meta.get("allowed_actions", [])
    assert meta.get("fallback_applied") is True


@patch("skills.balatro.session.ollama.chat")
def test_call_planner_normalizes_common_action_aliases(mock_chat):
    mock_chat.return_value = {
        "message": {
            "content": '{"synergy_evaluation":"","action":"buy shop","indices":[1],"reasoning":"alias"}'
        }
    }

    parsed = _call_planner("test-context", allowed_actions={"continue", "buy_shop"})

    assert parsed["action"] == "buy_shop"
    assert parsed["indices"] == [1]


@patch("skills.balatro_bot.modules.balatro_telemetry.log_decision")
def test_log_decision_safe_forwards_planner_meta(mock_log_decision):
    from skills.balatro.session import _log_decision_safe

    raw_state = {"state": "shop", "ante_num": 2}
    planner_meta = {
        "phase_id": "shop",
        "allowed_actions": ["buy_shop", "continue"],
        "raw_action": "buy card",
        "normalized_action": "buy_card",
        "fallback_applied": True,
        "fallback_reason": "unknown action",
    }

    _log_decision_safe(
        "continue",
        False,
        "fallback",
        raw_state,
        planner_meta=planner_meta,
    )

    mock_log_decision.assert_called_once()
    kwargs = mock_log_decision.call_args.kwargs
    assert kwargs.get("planner_meta") == planner_meta


@patch("skills.balatro.session.ollama.chat")
def test_call_planner_default_contains_telemetry_on_parse_failure(mock_chat):
    mock_chat.return_value = {"message": {"content": "not-json"}}

    parsed = _call_planner(
        "test-context",
        allowed_actions={"continue", "buy_shop"},
        phase_id="shop",
    )

    assert parsed["action"] == "continue"
    meta = parsed.get("_planner_telemetry")
    assert isinstance(meta, dict)
    assert meta.get("phase_id") == "shop"
    assert meta.get("fallback_applied") is True


def test_tick_context_shop_cache_reused_for_same_state_and_deck():
    raw_state = {
        "state": "shop",
        "money": 20,
        "ante_num": 1,
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
        "packs": {"cards": []},
        "vouchers": {"cards": []},
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
    }

    ctx = TickContext.create(raw_state)
    cache_a = ctx.ensure_shop_cache("CHECKERED")
    cache_b = ctx.ensure_shop_cache("CHECKERED")

    assert cache_a is not None
    assert cache_a is cache_b
    assert len(cache_a.shop_summaries) == 1


def test_tick_context_shop_cache_invalidated_on_state_update():
    raw_state = {
        "state": "shop",
        "money": 20,
        "ante_num": 1,
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
        "packs": {"cards": []},
        "vouchers": {"cards": []},
        "jokers": {"size": 5, "cards": []},
        "hand": {"cards": []},
    }
    next_state = {
        **raw_state,
        "money": 12,
    }

    ctx = TickContext.create(raw_state)
    cache_before = ctx.ensure_shop_cache("CHECKERED")
    ctx.update_raw_state(next_state)
    cache_after = ctx.ensure_shop_cache("CHECKERED")

    assert cache_before is not None
    assert cache_after is not None
    assert cache_before is not cache_after
    assert cache_after.state_hash != cache_before.state_hash


def test_run_loop_forces_continue_when_phase_directive_missing_non_game_over(mock_iris):
    from skills.balatro.actions.base import ActionResult

    session = BalatroSession(mock_iris, None)
    session.phase_router = MagicMock()
    session.phase_router.get_directive.return_value = None
    session.prompt_builder = MagicMock()
    session.prompt_builder.build.return_value = "planner-context"

    controller = MagicMock()
    controller.refresh_state.return_value = True
    controller.current_state = None
    controller.raw_state = {
        "state": "mystery_phase",
        "hand": {"cards": []},
        "jokers": {"cards": []},
        "round": {},
    }
    controller.get_context_for_llm.return_value = "{}"
    session.controller = controller

    class _DummyContinueAction:
        def execute(self, _ctx):
            session.stop_event.set()
            return ActionResult(succeeded=True, error=None, persona_reasoning="ok")

    planner_output = {
        "action": "buy_shop",
        "indices": [1],
        "reasoning": "invalid for unknown phase",
        "_planner_telemetry": {},
    }

    with patch("skills.balatro.session._call_planner", return_value=planner_output), patch(
        "skills.balatro.actions.ActionRegistry.get", return_value=_DummyContinueAction
    ) as mock_registry_get, patch(
        "skills.balatro.session.time.sleep", return_value=None
    ), patch("skills.balatro.session.threading.Thread") as mock_thread:
        mock_thread.return_value.start.return_value = None
        session.run_loop()

    mock_registry_get.assert_called_with("continue")


def test_run_loop_failed_action_clears_state_hash_for_immediate_replan(mock_iris):
    from skills.balatro.actions.base import ActionResult
    from skills.balatro.router import PhaseDirective

    session = BalatroSession(mock_iris, None)
    session.phase_router = MagicMock()
    session.phase_router.get_directive.return_value = PhaseDirective(
        phase_id="shop",
        allowed_actions=["reroll", "continue"],
        guidance="shop phase",
        utility_actions=[],
    )
    session.prompt_builder = MagicMock()
    session.prompt_builder.build.return_value = "planner-context"

    controller = MagicMock()
    controller.refresh_state.return_value = True
    controller.current_state = None
    controller.raw_state = {
        "state": "shop",
        "shop": {"cards": []},
        "packs": {"cards": []},
        "vouchers": {"cards": []},
        "consumeables": {"cards": []},
        "jokers": {"cards": []},
        "hand": {"cards": []},
        "round": {"reroll_cost": 1},
        "money": 10,
    }
    controller.get_context_for_llm_with_joker_scores.return_value = "{}"
    session.controller = controller

    class _DummyFailedAction:
        def execute(self, _ctx):
            session.stop_event.set()
            return ActionResult(
                succeeded=False,
                error="Action Denied: test failure",
                persona_reasoning="blocked",
            )

    planner_output = {
        "action": "reroll",
        "indices": [],
        "reasoning": "test",
        "_planner_telemetry": {},
    }

    with patch("skills.balatro.session._call_planner", return_value=planner_output), patch(
        "skills.balatro.actions.ActionRegistry.get", return_value=_DummyFailedAction
    ), patch("skills.balatro.session.time.sleep", return_value=None), patch(
        "skills.balatro.session.threading.Thread"
    ) as mock_thread:
        mock_thread.return_value.start.return_value = None
        session.run_loop()

    assert session.last_state_hash is None
