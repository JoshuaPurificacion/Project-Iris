"""Action classes for Balatro skill."""

from .base import ActionRegistry, ActionContext, ActionResult
from .play import PlayHandAction, DiscardAction
from .shop import BuyShopAction, BuyPackAction, BuyVoucherAction, StartRunAction, RerollAction
from .blind import BlindSelectAction, SkipBlindAction, SkipAction
from .pack import ChoosePackAction, SkipPackAction
from .utility import (
    SellJokerAction,
    SellConsumableAction,
    UseConsumableAction,
    RearrangeJokerAction,
    RearrangeConsumableAction,
)
from .continue_action import ContinueAction, CashOutAction, CashoutAction, CashAction

# Register all actions
ActionRegistry.register(PlayHandAction)
ActionRegistry.register(DiscardAction)
ActionRegistry.register(BuyShopAction)
ActionRegistry.register(BuyPackAction)
ActionRegistry.register(BuyVoucherAction)
ActionRegistry.register(RerollAction)
ActionRegistry.register(StartRunAction)
ActionRegistry.register(BlindSelectAction)
ActionRegistry.register(SkipBlindAction)
ActionRegistry.register(SkipAction)
ActionRegistry.register(ChoosePackAction)
ActionRegistry.register(SkipPackAction)
ActionRegistry.register(SellJokerAction)
ActionRegistry.register(SellConsumableAction)
ActionRegistry.register(UseConsumableAction)
ActionRegistry.register(RearrangeJokerAction)
ActionRegistry.register(RearrangeConsumableAction)
ActionRegistry.register(ContinueAction)
ActionRegistry.register(CashOutAction)
ActionRegistry.register(CashoutAction)
ActionRegistry.register(CashAction)
