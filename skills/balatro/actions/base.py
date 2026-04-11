import abc
import typing
from dataclasses import dataclass
from typing import TYPE_CHECKING
from core.logger import log_system

if TYPE_CHECKING:
    from skills.balatro.session import TickContext
else:
    TickContext = None


@dataclass
class ActionResult:
    """Result of action execution."""

    succeeded: bool
    error: str | None = None
    persona_reasoning: str = ""


@dataclass
class ActionContext:
    """Context for action execution."""

    session: typing.Any  # BalatroSession
    tick_context: "TickContext"
    planner_output: dict  # Raw planner JSON
    controller: typing.Any  # GameController
    state: str  # Current state string


class BaseAction(abc.ABC):
    """Abstract base class for all Balatro actions."""

    @abc.abstractmethod
    def execute(self, ctx: ActionContext) -> "ActionResult":
        """Execute the action and return result."""
        pass

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Name of the action (matches planner action tag)."""
        pass

    def _validate_state(self, ctx: ActionContext, allowed_states: list[str]) -> bool:
        """Check if current state allows this action."""
        current_state = ctx.state.lower()
        for allowed in allowed_states:
            if allowed in current_state:
                return True
        return False

    def _log_success(self, ctx: ActionContext, details: str = "") -> None:
        """Log successful action execution."""
        from skills.balatro.session import _log_decision_safe

        log_system(f"[Balatro] {self.name} succeeded: {details}")
        _log_decision_safe(self.name, True, None, ctx.tick_context.raw_state)

    def _log_error(self, ctx: ActionContext, error: str) -> None:
        """Log failed action execution."""
        from skills.balatro.session import _log_decision_safe

        log_system(f"[Balatro] {self.name} failed: {error}")
        _log_decision_safe(self.name, False, error, ctx.tick_context.raw_state)

    def _get_args(self, ctx: ActionContext) -> list[int]:
        """Get planner indices as list of ints."""
        return ctx.planner_output.get("indices", [])


class ActionRegistry:
    """Registry for action classes."""

    _actions: dict[str, typing.Type[BaseAction]] = {}

    @classmethod
    def register(cls, action_class: typing.Type[BaseAction]):
        """Register an action class."""
        # Create temporary instance to get the name property value
        temp_instance = action_class()
        action_name = temp_instance.name
        cls._actions[action_name] = action_class
        return action_class

    @classmethod
    def get(cls, action_name: str) -> typing.Type[BaseAction] | None:
        """Get action class by name."""
        return cls._actions.get(action_name)

    @classmethod
    def create(cls, action_name: str) -> BaseAction | None:
        """Create an instance of the action."""
        action_class = cls.get(action_name)
        if action_class:
            return action_class()
        return None

    @classmethod
    def is_registered(cls, action_name: str) -> bool:
        """Check if action is registered."""
        return action_name in cls._actions

    @classmethod
    def get_all_names(cls) -> list[str]:
        """Get all registered action names."""
        return list(cls._actions.keys())
