"""
Project Iris - Pokemon Skill
Gives Iris the ability to launch and play Pokemon FireRed autonomously.

How it fits into Project-Iris:
  1. Add "pokemon" context to TOOL_CONTEXTS in iris_agent.py
  2. Import and wire handle_pokemon_tool() in wake_up.py alongside
     the existing quiz and omnisense handlers
  3. Iris can then say "play Pokemon" or be told "launch FireRed"
     and she'll open BizHawk and start navigating by herself

The agent loop runs in a background thread so Iris can still talk,
comment on the game, and respond to the user while playing.
"""

import os
import subprocess
import threading
import time
from typing import Optional

# ── Paths — adjust if your layout differs ─────────────────────────────────
_PROJECT_POKEMON = r"C:\Users\Josh\Documents\Project-Iris-Pokemon"
_LAUNCH_BAT      = os.path.join(_PROJECT_POKEMON, "launch_bizhawk.bat")
_RUN_EMULATOR    = os.path.join(_PROJECT_POKEMON, "run_emulator.py")
_PYTHON          = r"C:\Users\Josh\Documents\Project-Iris\.venv\Scripts\python.exe"


# ── Shared state ──────────────────────────────────────────────────────────

class _PokemonState:
    def __init__(self):
        self.running       = False
        self.map_name      = ""
        self.player_pos    = (0, 0)
        self.current_goal  = None
        self.frames_run    = 0
        self._agent        = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event   = threading.Event()

_state = _PokemonState()


# ── Agent thread ──────────────────────────────────────────────────────────

def _agent_loop(goal_xy: Optional[tuple]):
    """Runs the emulator server + agent loop in a background thread."""
    import sys
    sys.path.insert(0, _PROJECT_POKEMON)

    from core.emulator import create_emulator, reset
    from core.agent import Agent
    from config import DECOMP_PATH

    try:
        emu   = create_emulator()   # blocks until BizHawk connects
        reset(emu)

        agent = Agent(emu, DECOMP_PATH)
        _state._agent = agent

        if goal_xy:
            agent.set_goal(goal_xy)

        while not _state._stop_event.is_set():
            agent.step()
            _state.frames_run  += 1
            _state.map_name     = agent.get_map_name()
            _state.player_pos   = agent.get_player_xy()

    except Exception as e:
        print(f"[PokemonSkill] Agent loop error: {e}")
    finally:
        _state.running = False
        print("[PokemonSkill] Agent loop ended.")


# ── Public tool functions ─────────────────────────────────────────────────

def launch_game(goal: str = "16,13") -> str:
    """
    Launch BizHawk and start the agent.
    goal: "x,y" tile coordinate string, default is Oak's lab entrance.
    Returns a status string Iris can speak.
    """
    if _state.running:
        return "I'm already playing Pokemon!"

    # Parse goal
    goal_xy = None
    try:
        x, y   = goal.split(",")
        goal_xy = (int(x.strip()), int(y.strip()))
    except Exception:
        goal_xy = (16, 13)

    # Launch BizHawk in the background
    try:
        subprocess.Popen(
            _LAUNCH_BAT,
            shell=True,
            cwd=_PROJECT_POKEMON,
        )
    except Exception as e:
        return f"Couldn't open BizHawk: {e}"

    # Start Python agent server in background thread
    _state._stop_event.clear()
    _state.running   = True
    _state.frames_run = 0
    _state.current_goal = goal_xy

    t = threading.Thread(
        target=_agent_loop,
        args=(goal_xy,),
        daemon=True,
        name="iris-pokemon-agent"
    )
    _state._thread = t
    t.start()

    return (f"Alright, launching Pokemon FireRed! "
            f"I'll navigate to tile {goal_xy[0]}, {goal_xy[1]}. "
            f"Give me a second for BizHawk to connect.")


def get_game_status() -> str:
    """Return a readable status string Iris can speak or display."""
    if not _state.running:
        return "I'm not playing Pokemon right now."
    ready = _state._agent is not None and _state._agent.is_game_ready()
    if not ready:
        return "BizHawk is connected but I'm still waiting for the game to start."
    return (f"I'm on {_state.map_name} at position "
            f"({_state.player_pos[0]}, {_state.player_pos[1]}), "
            f"{_state.frames_run} frames in.")


def set_objective(goal: str) -> str:
    """
    Change Iris's navigation goal mid-game.
    goal: "x,y" coordinate string.
    """
    if not _state.running or _state._agent is None:
        return "I'm not in game yet — launch first."
    try:
        x, y = goal.split(",")
        xy   = (int(x.strip()), int(y.strip()))
    except Exception:
        return "Give me the goal as x,y — like 16,13."

    _state._agent.set_goal(xy)
    _state.current_goal = xy
    return f"New goal set: heading to {xy[0]}, {xy[1]}."


def stop_game() -> str:
    """Stop the agent and close the game session."""
    if not _state.running:
        return "I wasn't playing anyway."
    _state._stop_event.set()
    _state.running = False
    return "Stopping Pokemon. See you next time!"


# ── Tool definitions for TOOL_CONTEXTS in iris_agent.py ──────────────────

POKEMON_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "launch_game",
            "description": (
                "Launch Pokemon FireRed and start playing autonomously. "
                "Call when user says 'play Pokemon', 'open FireRed', "
                "'start the game', or any variation of wanting Iris to play."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "Target tile as 'x,y'. Default is Oak's lab entrance (16,13)."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_game_status",
            "description": "Get current Pokemon game status — map, position, frames run.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_objective",
            "description": "Change Iris's navigation goal while the game is running.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "New target tile as 'x,y'."
                    }
                },
                "required": ["goal"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "stop_game",
            "description": "Stop playing Pokemon and shut down the agent.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
]


# ── Tool dispatcher (call this from wake_up.py / iris_agent.py) ──────────

def handle_pokemon_tool(tool_name: str, args: dict) -> str:
    """Route a tool call from Iris to the right function."""
    if   tool_name == "launch_game":    return launch_game(args.get("goal", "16,13"))
    elif tool_name == "get_game_status": return get_game_status()
    elif tool_name == "set_objective":  return set_objective(args.get("goal", ""))
    elif tool_name == "stop_game":      return stop_game()
    return f"Unknown pokemon tool: {tool_name}"
