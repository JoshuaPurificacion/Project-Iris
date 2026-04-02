# Balatro Bot Architecture

The Balatro bot is an autonomous decision-making engine designed to play the game Balatro optimally, utilizing heuristics, game state evaluation, and a defined strategy pattern.

## Core Implementations & Strategy

### 1. Checkered Deck Enforcement
The bot strictly forces a **Checkered Deck** strategy. This emphasizes Flush-based builds (Spades and Hearts), simplifying the hand probability distribution and enabling specialized scoring strategies around Flush synergies.

*   **Enforcement Point**: Decks are hardcoded to the Checkered deck in the router and prompt builder during the main menu, ensuring all subsequent phases are tailored to this structure.

### 2. Shop Guidance & Sell-to-Upgrade Logic
The bot doesn't just evaluate cards in isolation; it understands its capacity limits. When the Joker slots are full and a high-value scaling Joker appears in the shop, the bot uses **Sell-to-Upgrade Guidance**.

*   Explicit Output: Instead of just rating a Joker highly, the bot outputs structured recommendations like `-> RECOMMENDED: sell_joker [index] then buy_shop [index]`.
*   This logic bridges gap between mathematical evaluation and actionable in-game steps, preventing the bot from "freezing" when deciding between keeping a mediocre Joker versus acquiring a powerful late-game piece.

### 3. Conditional Joker Scoring (Scaling Math)
Jokers are scored based on the *current actual game state*, rather than raw base values.

*   `_estimate_scaling_joker_bonus`: Dynamically calculates the value of Jokers that scale (e.g., Hologram scaling off added cards, Steel Joker scaling off Steel cards in hand).
*   By injecting `raw_state` into the calculation algorithms, the bot determines the true mathematical impact of synergizing pieces.

### 4. Planet Prioritization
The bot aggressively weights **Jupiter** (the Flush-leveling planet) due to the Checkered Deck lock. All consumable evaluations factor in this synergy to continuously elevate the run's scoring baseline.

## Key Modules & Classes

### `skills/balatro_bot/modules/algorithms.py`
The mathematical heart of the bot.
*   **Functions**:
    *   `_state_cards(raw_state)`: Parses the live JSON deck state representation into operable python structures.
    *   `_estimate_scaling_joker_bonus(joker_key, raw_state)`: Computes situational scaling values.
    *   `_lineup_strategic_bonus(joker_name, current_jokers)`: Analyzes synergistic effects between current and prospective Jokers.
    *   Consumable Evaluators: Hard-bias towards `Jupiter` and Tarot cards that augment Heart/Spade infrastructure.

### `skills/balatro/session.py`
Manages state persistence and constructs actionable instructions for the LLM based on live memory.
*   **Methods**:
    *   `_build_shop_strategy_block(raw_state)`: Connects algorithms directly to prompt injections, actively recommending `sell-to-upgrade` moves based on comparative evaluations.

### `skills/balatro/router.py` & `skills/balatro/actions/*.py`
Handles transitions across game phases (Blind selection, playing hands, shopping).
*   Enforces unconditional structural decisions (e.g., forcing the `CHECKERED` deck selection in `actions/shop.py`).

### Testing Infrastructure (`tests/test_balatro_algorithms.py`)
Ensures heuristics and routing logic remain mathematically consistent through focused unit tests targeting conditional scoring branches and deck biases.

## Flow of Data

1.  **State Ingestion**: Raw JSON state from the Balatro API is captured by the main bot loop and passed to `session.py`.
2.  **Algorithmic Analysis**: `algorithms.py` recursively examines hand contents, current Jokers, Shop contents, and Pack options against historical metadata and synergy logic.
3.  **Strategy Formulation**: `session.py` compiles these mathematical insights into human-readable strategic imperatives (like prioritize Jupiter, or Sell Joker 2 for Shop item 1).
4.  **Action Execution**: Routing logic (e.g., `router.py`, `shop.py`) intercepts these imperatives, formats them into API-compliant commands, and executes them within the game window.
