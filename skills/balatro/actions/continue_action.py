import typing
from .base import BaseAction, ActionContext, ActionResult
from core.logger import log_system


class ContinueAction(BaseAction):
    """Execute continue action (state-dependent)."""

    @property
    def name(self) -> str:
        return "continue"

    def execute(self, ctx: ActionContext) -> ActionResult:
        current_state = ctx.state.lower()

        # Handle different states
        if "round_eval" in current_state:
            return self._handle_round_eval(ctx)
        elif "shop" in current_state:
            return self._handle_shop(ctx)
        elif "blind" in current_state:
            return self._handle_blind(ctx)
        else:
            error = f"Invalid state '{current_state}' for continue."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

    def _handle_round_eval(self, ctx: ActionContext) -> ActionResult:
        """Handle continue in ROUND_EVAL state (cash out)."""
        import time
        log_system("[Balatro] Continue in ROUND_EVAL: cashing out.")

        # Retry up to 3 times — cash_out can fail transiently during scoring animation
        for attempt in range(1, 4):
            cash_out_result = ctx.controller.client._call("cash_out")
            if cash_out_result is not None:
                break
            log_system(f"[Balatro] cash_out attempt {attempt}/3 failed, retrying in 2s...")
            time.sleep(2.0)

        if cash_out_result is None:
            # Last resort: force next_round to break the round_eval softlock
            log_system("[Balatro] cash_out failed 3 times — forcing proceed_next to escape round_eval.")
            ctx.controller.client.proceed_next()
            error = "cash_out failed after 3 retries; forced proceed_next to escape."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        self._log_success(ctx, "cashed out")
        return ActionResult(
            succeeded=True,
            error=None,
            persona_reasoning=ctx.planner_output.get("reasoning", ""),
        )

    def _handle_shop(self, ctx: ActionContext) -> ActionResult:
        """Handle continue in SHOP state (advance to next round)."""
        log_system("[Balatro] Continue in SHOP: advancing to next round.")
        proceeded = ctx.controller.client.proceed_next()

        if proceeded:
            self._log_success(ctx, "advanced to next round")
            return ActionResult(
                succeeded=True,
                error=None,
                persona_reasoning=ctx.planner_output.get("reasoning", ""),
            )
        else:
            error = "next_round failed during continue."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

    def _handle_blind(self, ctx: ActionContext) -> ActionResult:
        """Handle continue in BLIND state (select or skip blind)."""
        if ctx.controller.client.select():
            log_system("[Balatro] Continue in BLIND SELECT: selected current blind.")
            self._log_success(ctx, "selected blind")
            return ActionResult(
                succeeded=True,
                error=None,
                persona_reasoning=ctx.planner_output.get("reasoning", ""),
            )
        elif ctx.controller.client.skip():
            log_system("[Balatro] Continue in BLIND SELECT: skipped blind.")
            self._log_success(ctx, "skipped blind")
            return ActionResult(
                succeeded=True,
                error=None,
                persona_reasoning=ctx.planner_output.get("reasoning", ""),
            )
        else:
            error = "select/skip failed during continue."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")


class CashOutAction(BaseAction):
    """Execute cash_out, cashout, or cash action."""

    @property
    def name(self) -> str:
        return "cash_out"

    def execute(self, ctx: ActionContext) -> ActionResult:
        # State validation
        if not self._validate_state(ctx, ["round_eval"]):
            error = f"cash_out requested outside round_eval state '{ctx.state}'."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        import time
        log_system("[Balatro] Cashing out via action tag.")

        # Retry up to 3 times — cash_out can fail transiently during scoring animation
        cash_out_result = None
        for attempt in range(1, 4):
            cash_out_result = ctx.controller.client._call("cash_out")
            if cash_out_result is not None:
                break
            log_system(f"[Balatro] cash_out attempt {attempt}/3 failed, retrying in 2s...")
            time.sleep(2.0)

        if cash_out_result is None:
            log_system("[Balatro] cash_out failed 3 times — forcing proceed_next to escape round_eval.")
            ctx.controller.client.proceed_next()
            error = "cash_out failed after 3 retries; forced proceed_next to escape."
            self._log_error(ctx, error)
            return ActionResult(succeeded=False, error=error, persona_reasoning="")

        self._log_success(ctx, "cashed out")
        return ActionResult(
            succeeded=True,
            error=None,
            persona_reasoning=ctx.planner_output.get("reasoning", ""),
        )


# Aliases for cashout and cash
class CashoutAction(CashOutAction):
    """Alias for cashout action (same as cash_out)."""

    @property
    def name(self) -> str:
        return "cashout"


class CashAction(CashOutAction):
    """Alias for cash action (same as cash_out)."""

    @property
    def name(self) -> str:
        return "cash"
