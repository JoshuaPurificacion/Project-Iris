# Balatro Development Log

This log tracks Balatro-specific implementation decisions, prompt changes, tests, and debugging notes.

---

## Session Date: April 2, 2026

### Focus: Shop Synergy Evaluation and Joker Effect Reasoning

### Context

Captured live Balatro state snapshots showed that both owned Jokers and shop Jokers include readable `value.effect` text, which is suitable for direct LLM comparison during shop decisions.

Example live effects captured:
- Owned Joker: `Played face cards give +30 Chips when scored`
- Shop Joker: `This Joker gains +3 Mult when any Booster Pack is skipped (Currently +0 Mult)`
- Shop Joker: `+100 Chips if played hand contains a Three of a Kind`

### Decisions

1. Keep the planner output JSON flat for compatibility with existing downstream parsing.
2. Add `synergy_evaluation` as a top-level field so it is generated before `action`.
3. Make shop guidance explicitly require the model to compare candidate Joker effects against currently owned Joker effects and the current build direction.
4. Preserve `value.effect` in the state JSON and prompt context so the model reads the actual card text instead of relying on a lookup table.

### Implementation Notes

- Updated `skills/balatro/session.py` to add `synergy_evaluation` to `PlannerOutput` and the system prompt contract.
- Updated `skills/balatro/router.py` to add a shop decision rule requiring synergy evaluation before purchase actions.
- Added shop-context coverage in `skills/balatro/prompt_builder.py` so the planner gets stronger availability constraints.
- Added support for both `consumeables` and `consumables` payload spellings in the Balatro code paths that read or manipulate consumable cards.

### Validation

- Added targeted tests for router guidance, planner schema compatibility, and real Joker effect text in prompt context.
- Ran the Balatro-focused test suite successfully after the changes.

### Useful Files

- [skills/balatro/session.py](../skills/balatro/session.py)
- [skills/balatro/router.py](../skills/balatro/router.py)
- [skills/balatro/prompt_builder.py](../skills/balatro/prompt_builder.py)
- [tests/test_balatro_router_prompt.py](../tests/test_balatro_router_prompt.py)
- [tests/test_balatro_session.py](../tests/test_balatro_session.py)
- [tests/test_balatro_algorithms.py](../tests/test_balatro_algorithms.py)