import requests
import logging
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

# --- Pydantic Models (The Data Contract) ---


class CardValue(BaseModel):
    """Inner value properties of a card or joker."""

    rank: Optional[str] = None
    suit: Optional[str] = None
    effect: Optional[str] = None


class CardItem(BaseModel):
    """Represents a single card, joker, or consumable."""

    id: int
    key: str
    label: str
    value: CardValue


class CardContainer(BaseModel):
    """Wrapper for arrays of items (hands, jokers, shops)."""

    count: int = 0
    cards: List[CardItem] = Field(default_factory=list)


class BlindState(BaseModel):
    """Information about a specific blind."""

    name: str
    status: str
    score: int
    effect: Optional[str] = ""


class BlindsContainer(BaseModel):
    """The current ante's three blinds."""

    small: Optional[BlindState] = None
    big: Optional[BlindState] = None
    boss: Optional[BlindState] = None


class RoundInfo(BaseModel):
    """Current round stats."""

    hands_left: int = 0
    discards_left: int = 0
    chips: int = 0


class BalatroState(BaseModel):
    """The master JSON-RPC game state."""

    state: str = Field(default="Unknown")
    money: int = Field(default=0)
    ante_num: int = Field(default=1)
    round: Optional[RoundInfo] = None
    blinds: Optional[BlindsContainer] = None
    jokers: Optional[CardContainer] = None
    hand: Optional[CardContainer] = None
    shop: Optional[CardContainer] = None
    vouchers: Optional[CardContainer] = None
    packs: Optional[CardContainer] = None

    # We ignore the full 52-card 'deck' array to save LLM context tokens.


# --- API Client ---
class BalatroClient:
    """JSON-RPC HTTP client for BalatroBot."""

    def __init__(
        self, host: str = "127.0.0.1", port: int = 12346, timeout: float = 10.0
    ):
        self.base_url = f"http://{host}:{port}/"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self._request_id = 1
        self._last_error: Optional[str] = None

    def _call(
        self, method: str, params: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
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
                error_msg = data["error"].get("message", str(data["error"]))
                logger.error(f"API Error ({method}): {data['error']}")
                self._last_error = error_msg
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

    def get_game_state(self) -> Optional[Dict[str, Any]]:
        return self._call("gamestate")

    def play_hand(self, card_ids: List[int]) -> tuple[bool, Optional[str]]:
        result = self._call("play", {"cards": card_ids})
        if result is None:
            return False, getattr(self, "_last_error", "Unknown API Error")
        return True, None

    def proceed_next(self) -> bool:
        """Trigger the next round or blind selection."""
        return self._call("next_round") is not None


# --- Game Controller ---
class GameController:
    """Maintains the validated, strongly-typed game state."""

    def __init__(self):
        self.client = BalatroClient()
        self.current_state: Optional[BalatroState] = None
        self.raw_state: dict = {}

    def refresh_state(self) -> bool:
        raw_data = self.client.get_game_state()
        if not raw_data:
            return False

        self.raw_state = raw_data
        try:
            self.current_state = BalatroState.model_validate(raw_data)
            return True
        except ValidationError as e:
            logger.error(f"Contract violation: {e}")
            return False

    def get_context_for_llm(self) -> str:
        """Dumps state while excluding bulky fields to save tokens."""
        if not self.current_state:
            return '{"error": "Game state unavailable."}'
        return self.current_state.model_dump_json(
            indent=2, exclude={"cards", "hands", "used_vouchers"}
        )
