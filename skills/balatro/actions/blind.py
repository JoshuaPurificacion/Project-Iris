import typing
from .base import BaseAction, ActionContext, ActionResult
from core.logger import log_system


class BlindSelectAction(BaseAction):
    """Execute select action for blind selection."""

    @property
    def name(self) -> str:
        return "select"

    def execute(self, ctx: ActionContext) -> ActionResult:
        if not self._validate_state(ctx, ["blind"]):
            error = f"select requested outside blind state '{ctx.state}'."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        if ctx.controller.client.select():
            self._log_success(ctx, "selected blind")
            return ActionResult(
                succeeded=True,
                error=None,
                persona_reasoning=ctx.planner_output.get("reasoning", ""),
            )
        else:
            error = "Blind select failed."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")


class SkipBlindAction(BaseAction):
    """Execute skip_blind or skip action."""

    @property
    def name(self) -> str:
        return "skip_blind"

    def execute(self, ctx: ActionContext) -> ActionResult:
        if not self._validate_state(ctx, ["blind"]):
            error = f"skip_blind requested outside blind state '{ctx.state}'."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        if ctx.controller.client.skip():
            self._log_success(ctx, "skipped blind")
            return ActionResult(
                succeeded=True,
                error=None,
                persona_reasoning=ctx.planner_output.get("reasoning", ""),
            )
        else:
            error = "Blind skip failed."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")


# Alias for "skip" action
class SkipAction(SkipBlindAction):
    """Alias for skip action (same as skip_blind)."""

    @property
    def name(self) -> str:
        return "skip"
