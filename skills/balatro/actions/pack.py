import typing
from .base import BaseAction, ActionContext, ActionResult
from core.logger import log_system


class ChoosePackAction(BaseAction):
    """Execute choose_pack action."""

    @property
    def name(self) -> str:
        return "choose_pack"

    def execute(self, ctx: ActionContext) -> ActionResult:
        # State validation
        if not self._validate_state(ctx, ["pack", "booster"]):
            error = f"choose_pack requested outside pack/booster state '{ctx.state}'."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Argument validation
        args = self._get_args(ctx)
        if not args:
            error = "choose_pack requested without pack arguments."
            self._log_error(ctx, error)
            # Auto-skip to prevent lock
            ctx.controller.client.pack(skip=True)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        # Convert to 0-based indices for the API
        try:
            pack_idx = int(args[0]) - 1
            targets = [int(t) - 1 for t in args[1:]] if len(args) > 1 else None
        except (ValueError, TypeError):
            error = "choose_pack indices must be integers."
            self._log_error(ctx, error)
            ctx.controller.client.pack(skip=True)  # Auto-skip to prevent lock
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        log_system(
            f"[Balatro] Choosing pack card (0-based) {pack_idx} with targets {targets}."
        )

        # Execute pack choice
        result = ctx.controller.client.pack(card=pack_idx, targets=targets)
        if not result:
            error = "Pack choice was rejected by the API."
            self._log_error(ctx, error)
            ctx.controller.client.pack(skip=True)  # Auto-skip to prevent lock
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        self._log_success(ctx, f"chose pack card {pack_idx}")
        return ActionResult(
            succeeded=True,
            error=None,
            persona_reasoning=ctx.planner_output.get("reasoning", ""),
        )


class SkipPackAction(BaseAction):
    """Execute skip_pack action."""

    @property
    def name(self) -> str:
        return "skip_pack"

    def execute(self, ctx: ActionContext) -> ActionResult:
        # State validation
        if not self._validate_state(ctx, ["pack", "booster"]):
            error = f"skip_pack requested outside pack/booster state '{ctx.state}'."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        log_system("[Balatro] Skipping pack.")
        skipped = ctx.controller.client.pack(skip=True)

        if skipped:
            self._log_success(ctx, "skipped pack")
            return ActionResult(
                succeeded=True,
                error=None,
                persona_reasoning=ctx.planner_output.get("reasoning", ""),
            )
        else:
            error = "skip_pack failed."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")
