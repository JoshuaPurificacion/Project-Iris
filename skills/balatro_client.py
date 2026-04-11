import json
import requests
import logging
from typing import Dict, Any, List
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

# --- Pydantic Models (The Data Contract) ---


class ContractModel(BaseModel):
    """Base contract model that preserves live RPC fields we do not model yet."""

    model_config = ConfigDict(extra="allow")


class CardValue(ContractModel):
    """Inner value properties of a card or joker."""

    rank: str | None = None
    suit: str | None = None
    effect: str | None = None


class CardItem(ContractModel):
    """Represents a single card, joker, or consumable."""

    id: int
    key: str
    label: str
    value: CardValue
    cost: Dict[str, int] | None = None  # Present in some API responses; None = unknown


class CardContainer(ContractModel):
    """Wrapper for arrays of items (hands, jokers, shops)."""

    count: int = 0
    cards: List[CardItem] = Field(default_factory=list)


class BlindState(ContractModel):
    """Information about a specific blind."""

    name: str
    status: str
    score: int
    effect: str | None = ""


class BlindsContainer(ContractModel):
    """The current ante's three blinds."""

    small: BlindState | None = None
    big: BlindState | None = None
    boss: BlindState | None = None


class RoundInfo(ContractModel):
    """Current round stats."""

    hands_left: int = 0
    discards_left: int = 0
    chips: int = 0
    reroll_cost: int = 0


class BalatroState(ContractModel):
    """The master JSON-RPC game state."""

    state: str = Field(default="Unknown")
    money: int = Field(default=0)
    ante_num: int = Field(default=1)
    round: RoundInfo | None = None
    blinds: BlindsContainer | None = None
    jokers: CardContainer | None = None
    hand: CardContainer | None = None
    shop: CardContainer | None = None
    vouchers: CardContainer | None = None
    pack: CardContainer | None = None
    packs: CardContainer | None = None
    consumeables: CardContainer | None = None

    # Optional explicit level metadata from API (strict explicit-data mode).
    # Keep as loose dicts because payload shape can vary across client versions.
    hand_levels: Dict[str, Any] | None = None
    planet_levels: Dict[str, Any] | None = None

    # We ignore the full 52-card 'deck' array to save LLM context tokens.


# --- API Client ---
class BalatroClient:
    """JSON-RPC HTTP client for BalatroBot."""

    def __init__(
        self, host: str = "127.0.0.1", port: int = 12346, timeout: float = 15.0
    ):
        self.base_url = f"http://{host}:{port}/"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self._request_id = 1
        self._last_error: str | None = None

    @property
    def last_error(self) -> str:
        """Return the most recent API/client error, if any."""
        return self._last_error or ""

    def _call(
        self, method: str, params: Dict[str, Any] | None = None
    ) -> Dict[str, Any] | None:
        """Handles the strict JSON-RPC 2.0 POST format."""
        self._last_error = None
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "id": self._request_id,
        }
        if params:
            payload["params"] = params

        self._request_id += 1

        try:
            response = self.session.post(
                self.base_url, json=payload, timeout=self.timeout
            )
            response.raise_for_status()
            data = response.json()

            if "error" in data:
                error_obj = data["error"]
                error_msg = error_obj.get("message", str(error_obj))
                error_name = ""
                error_data = error_obj.get("data")
                if isinstance(error_data, dict):
                    error_name = str(error_data.get("name") or "")
                logger.error(f"API Error ({method}): {data['error']}")
                self._last_error = (
                    f"{error_name}: {error_msg}" if error_name else error_msg
                )
                return None

            return data.get("result")

        except requests.exceptions.Timeout:
            logger.error("Timeout connecting to Balatro.")
            self._last_error = "Timeout"
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed '{method}': {e}")
            self._last_error = str(e)
            return None

    def get_game_state(self) -> Dict[str, Any] | None:
        return self._call("gamestate")

    def play_hand(self, card_ids: List[int]) -> tuple[bool, str | None]:
        result = self._call("play", {"cards": card_ids})
        if result is None:
            return False, getattr(self, "_last_error", "Unknown API Error")
        return True, None

    def select_blind(self, blind: str) -> bool:
        """Select a blind ('small', 'big', or 'boss') when in blind_select state."""
        return self._call("select_blind", {"blind": blind}) is not None

    def select(self) -> bool:
        """Select the current blind (BLIND_SELECT state)."""
        return self._call("select") is not None

    def skip(self) -> bool:
        """Skip the current blind (Small/Big only)."""
        return self._call("skip") is not None

    def proceed_next(self) -> bool:
        """Trigger the next round or blind selection."""
        return self._call("next_round") is not None

    def buy(
        self,
        card: int | None = None,
        voucher: int | None = None,
        pack: int | None = None,
    ) -> bool:
        """Buy a card, voucher, or pack from the shop."""
        params = {}
        if card is not None:
            params["card"] = card
        if voucher is not None:
            params["voucher"] = voucher
        if pack is not None:
            params["pack"] = pack
        return self._call("buy", params) is not None

    def pack(
        self,
        card: int | None = None,
        targets: List[int] | None = None,
        skip: bool = False,
    ) -> bool:
        """Interact with an open pack.

        If the chosen card requires targets (e.g. a Tarot in a Tarot pack),
        pass them via the 'targets' kwarg — they are forwarded directly in the
        'pack' payload.  The 'select' endpoint is state-gated to BLIND_SELECT
        and must not be called here.
        """
        params = {}
        if skip:
            params["skip"] = True
        else:
            if card is not None:
                params["card"] = card
            if targets:
                params["cards"] = targets  # Lua API key is 'cards', not 'targets'
        return self._call("pack", params) is not None

    def sell(self, card: int, area: str) -> bool:
        """Sell a Joker or consumable.

        The upstream API requires exactly one of 'joker' or 'consumable'
        as the payload key — the legacy {card, area} shape is rejected.

        Args:
            card: 0-based index of the card to sell within its area.
            area: Zone identifier — 'jokers'/'joker' or 'consumeables'/'consumables'.
        """
        zone = str(area or "").strip().lower()
        if zone in ("jokers", "joker"):
            params = {"joker": card}
        else:
            params = {"consumable": card}
        return self._call("sell", params) is not None

    def use(self, card: int, targets: List[int] | None = None) -> bool:
        """Use a consumable card (Tarot, Planet, Spectral, etc.).

        Targeted consumables (e.g. Death, The Hanged Man, Strength) pass their
        target hand-card indices directly in the 'use' payload.  No select
        pre-call is needed or valid outside of BLIND_SELECT state — attempting
        one causes a softlock.

        Args:
            card:    0-based index within the consumeables zone.
            targets: Optional list of 0-based hand card indices to target.
        """
        payload = {"consumable": card}
        if targets:
            payload["cards"] = targets  # Lua API key is 'cards', not 'targets'
        return self._call("use", payload) is not None

    def reroll(self) -> bool:
        """Reroll the shop. Requires the shop state (reroll_cost > 0) to be available."""
        return self._call("reroll") is not None

    def rearrange(self, card: int, to: int, location: str) -> bool:
        """Rearrange a card within a zone.

        API compatibility:
        - Newer servers require exactly one explicit zone key in params:
          'hand', 'jokers', or 'consumables'.
        - Older servers used a legacy 'location' string field.

        This method prefers the explicit-zone payload and falls back to the
        legacy payload if needed.

        Args:
            card: 0-based source index.
            to: 0-based destination index.
            location: Zone identifier ('hand', 'jokers', 'consumables', or legacy 'consumeables').
        """
        zone = str(location or "").strip().lower()
        if zone == "joker":
            zone = "jokers"
        elif zone in {"consumable", "consumeables", "consumables"}:
            zone = "consumables"
        elif zone not in {"hand", "jokers"}:
            self._last_error = f"Invalid rearrange location: {location!r}"
            return False

        modern_payload = {"card": card, "to": to, zone: []}
        if self._call("rearrange", modern_payload) is not None:
            return True

        # Backward compatibility for older location-based RPC variants.
        legacy_zone = "consumeables" if zone == "consumables" else zone
        legacy_payload = {"card": card, "to": to, "location": legacy_zone}
        return self._call("rearrange", legacy_payload) is not None


# --- Game Controller ---
class GameController:
    """Maintains the validated, strongly-typed game state."""

    def __init__(self):
        self.client = BalatroClient()
        self.current_state: BalatroState | None = None
        self.raw_state: dict = {}

    def refresh_state(self) -> bool:
        raw_data = self.client.get_game_state()
        if not raw_data:
            return False

        try:
            validated = BalatroState.model_validate(raw_data)
        except ValidationError as e:
            logger.error(f"Contract violation: {e}")
            self.raw_state = {}
            self.current_state = None
            return False

        self.raw_state = raw_data
        self.current_state = validated
        return True

    def get_context_for_llm(self) -> str:
        """Dumps state while excluding bulky fields to save tokens.

        The 'effect' field on CardValue is intentionally retained so the
        Planner can read card behavior directly from game state JSON without
        relying on a static prompt lookup table.
        """
        if not self.current_state:
            return '{"error": "Game state unavailable."}'

        excludes = {"cards", "hands", "used_vouchers"}
        state_str = (self.current_state.state or "").lower()
        # Strip shop inventory from phases where the shop is not open.
        # Without this, shop data leaks into round_eval and blind prompts,
        # causing the LLM to attempt buy_shop in invalid phases.
        NON_SHOP_PHASES = ("pack", "booster", "round_eval", "blind")
        if any(phase in state_str for phase in NON_SHOP_PHASES):
            excludes.add("shop")

        return self.current_state.model_dump_json(indent=2, exclude=excludes)

    def get_context_for_llm_with_joker_scores(self, joker_scorer) -> str:
        """Like get_context_for_llm(), but appends advisor labels to shop cards.

        Each shop card that is a Joker or consumable gets an 'advisor' field
        injected into its JSON representation, e.g. "advisor": "HIGH SYNERGY".
        The LLM can use these labels to make more informed purchase decisions.

        Args:
            joker_scorer: Callable matching BalatroAlgorithm.score_joker_value's
                          signature: (joker, current_jokers, hand_stats) -> str.
        """
        if not self.current_state:
            return '{"error": "Game state unavailable."}'

        # Build base state dict including shop cards (not excluded)
        excludes = {"hands", "used_vouchers"}
        state_dict = json.loads(
            self.current_state.model_dump_json(indent=2, exclude=excludes)
        )

        # Gather current joker list and hand stats for the scorer
        joker_container = self.raw_state.get("jokers") or {}
        current_jokers = (
            joker_container.get("cards", [])
            if isinstance(joker_container, dict)
            else []
        )
        joker_count = len(current_jokers)

        # Estimate total chips from round info (used for chip-threshold scoring)
        round_info = self.raw_state.get("round") or {}
        total_chips = round_info.get("chips", 0) if isinstance(round_info, dict) else 0

        hand_stats = {"total_chips": total_chips, "joker_count": joker_count}
        hand_container = self.raw_state.get("hand") or {}
        hand_cards = (
            hand_container.get("cards", []) if isinstance(hand_container, dict) else []
        )
        deck_name = str(self.raw_state.get("deck") or "")

        # Annotate each shop card with an advisor label
        shop_section = state_dict.get("shop") or {}
        shop_cards = (
            shop_section.get("cards") if isinstance(shop_section, dict) else None
        )

        if isinstance(shop_cards, list):
            for card in shop_cards:
                if isinstance(card, dict):
                    try:
                        try:
                            label = joker_scorer(
                                card,
                                current_jokers,
                                hand_stats,
                                deck_name=deck_name,
                                hand_cards=hand_cards,
                            )
                        except TypeError:
                            # Backward compatibility for scorers with the older signature.
                            label = joker_scorer(card, current_jokers, hand_stats)
                        card["advisor"] = label
                    except Exception:
                        card["advisor"] = "SITUATIONAL"

        return json.dumps(state_dict, indent=2)
