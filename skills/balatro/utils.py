def _safe_int(value, default: int = 0) -> int:
    """Best-effort integer coercion for loose RPC payloads."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_state_cards(raw_state: dict, container_name: str) -> list[dict]:
    """Safely read a card-array container from raw_state."""
    candidate_names = [container_name]
    if container_name == "consumeables":
        candidate_names.append("consumables")
    elif container_name == "consumables":
        candidate_names.append("consumeables")

    for name in candidate_names:
        container = raw_state.get(name) or {}
        if not isinstance(container, dict):
            continue

        cards = container.get("cards", [])
        if not isinstance(cards, list):
            continue

        return [card for card in cards if isinstance(card, dict)]

    return []


def _format_card(card: dict) -> str:
    """Format a single card as rank+suit (e.g., A♠, 9♦)."""
    if not isinstance(card, dict):
        return "?"
    value = card.get("value") or {}
    rank = value.get("rank", "")
    suit = value.get("suit", "")
    if rank and suit:
        suit_symbol = {
            "Spades": "♠",
            "S": "♠",
            "Hearts": "♥",
            "H": "♥",
            "Diamonds": "♦",
            "D": "♦",
            "Clubs": "♣",
            "C": "♣",
        }.get(suit, suit)
        return f"{rank}{suit_symbol}"
    return card.get("label", "?")


def _format_hand(hand_cards: list[dict]) -> str:
    """Format hand cards as [1] rank+suit, [2] rank+suit, ..."""
    if not hand_cards:
        return "empty"
    return ", ".join(
        f"[{i}]{_format_card(c)}" for i, c in enumerate(hand_cards, start=1)
    )
