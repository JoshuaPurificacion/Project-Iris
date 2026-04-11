import json

from skills.balatro.router import PhaseRouter, PhaseDirective
from skills.balatro.prompt_builder import PlannerContextBuilder
from skills.balatro.session import TickContext, PLANNER_SYSTEM_PROMPT


def _base_shop_state() -> dict:
    return {
        "state": "shop",
        "ante_num": 2,
        "money": 0,
        "round": {"reroll_cost": 5},
        "shop": {"cards": []},
        "packs": {"cards": []},
        "vouchers": {"cards": []},
        "consumeables": {"cards": []},
        "jokers": {"cards": []},
    }


def test_shop_directive_blocks_unavailable_actions_when_empty():
    tick_context = TickContext.create(_base_shop_state())

    directive = PhaseRouter.get_directive(tick_context)

    assert directive is not None
    assert "continue" in directive.full_allowed_actions
    assert "buy_shop" not in directive.full_allowed_actions
    assert "buy_pack" not in directive.full_allowed_actions
    assert "buy_voucher" not in directive.full_allowed_actions
    assert "reroll" not in directive.full_allowed_actions
    assert "No valid shop action besides continue" in directive.guidance


def test_shop_directive_allows_actions_when_inventory_exists():
    state = _base_shop_state()
    state["money"] = 10
    state["round"] = {"reroll_cost": 5}
    state["shop"] = {
        "cards": [
            {
                "label": "Neptune",
                "key": "c_neptune",
                "value": {"effect": ""},
                "cost": {"buy": 3},
            }
        ]
    }

    tick_context = TickContext.create(state)
    directive = PhaseRouter.get_directive(tick_context)

    assert directive is not None
    assert "buy_shop" in directive.full_allowed_actions
    assert "reroll" in directive.full_allowed_actions


def test_prompt_builder_injects_empty_shop_constraints():
    tick_context = TickContext.create(_base_shop_state())
    builder = PlannerContextBuilder()
    phase_directive = PhaseDirective(
        phase_id="shop",
        allowed_actions=["continue"],
        guidance="Shop phase",
        utility_actions=[],
    )

    context = builder.build(
        tick_context=tick_context,
        phase_directive=phase_directive,
        run_profile={},
        state_json="{}",
        session_memory=[],
    )

    assert "shop.cards is empty. Do NOT output buy_shop." in context
    assert "No valid shop action besides continue exists." in context


def test_shop_directive_requires_synergy_evaluation_before_action():
    state = _base_shop_state()
    state["shop"] = {
        "cards": [
            {
                "label": "Wily Joker",
                "key": "j_wily",
                "value": {
                    "effect": "+100 Chips if played hand contains a Three of a Kind"
                },
                "cost": {"buy": 4, "sell": 2},
            }
        ]
    }
    state["jokers"] = {
        "cards": [
            {
                "label": "Scary Face",
                "key": "j_scary_face",
                "value": {"effect": "Played face cards give +30 Chips when scored"},
                "cost": {"buy": 4, "sell": 2},
            }
        ]
    }

    directive = PhaseRouter.get_directive(TickContext.create(state))

    assert directive is not None
    assert "SHOP DECISION RULE" in directive.guidance
    assert "synergy_evaluation" in directive.guidance
    assert "value.effect" in directive.guidance


def test_planner_system_prompt_includes_synergy_evaluation_first():
    synergy_pos = PLANNER_SYSTEM_PROMPT.find('"synergy_evaluation"')
    action_pos = PLANNER_SYSTEM_PROMPT.find('"action"')

    assert synergy_pos != -1
    assert action_pos != -1
    assert synergy_pos < action_pos


def test_planner_system_prompt_warns_field_names_are_not_actions():
    assert "JSON key names are not actions" in PLANNER_SYSTEM_PROMPT
    assert (
        'Never set action to "indices", "reasoning", or "synergy_evaluation"'
        in PLANNER_SYSTEM_PROMPT
    )


def test_prompt_builder_keeps_real_joker_effect_text_in_context():
    state = _base_shop_state()
    state["jokers"] = {
        "cards": [
            {
                "id": 1239,
                "label": "Scary Face",
                "set": "JOKER",
                "value": {"effect": "Played face cards give +30 Chips when scored"},
                "key": "j_scary_face",
                "cost": {"buy": 4, "sell": 2},
            }
        ]
    }
    state["shop"] = {
        "cards": [
            {
                "id": 1234,
                "label": "Red Card",
                "set": "JOKER",
                "value": {
                    "effect": "This Joker gains +3 Mult when any Booster Pack is skipped (Currently +0 Mult)"
                },
                "key": "j_red_card",
                "cost": {"buy": 5, "sell": 2},
            },
            {
                "id": 1235,
                "label": "Wily Joker",
                "set": "JOKER",
                "value": {
                    "effect": "+100 Chips if played hand contains a Three of a Kind"
                },
                "key": "j_wily",
                "cost": {"buy": 4, "sell": 2},
            },
        ]
    }

    tick_context = TickContext.create(state)
    directive = PhaseRouter.get_directive(tick_context)
    assert directive is not None

    context = PlannerContextBuilder().build(
        tick_context=tick_context,
        phase_directive=directive,
        run_profile={},
        state_json=json.dumps(state, indent=2),
        session_memory=[],
    )

    assert "Played face cards give +30 Chips when scored" in context
    assert "This Joker gains +3 Mult when any Booster Pack is skipped" in context
    assert "+100 Chips if played hand contains a Three of a Kind" in context
