# modes/default.py

SYSTEM_PROMPT = """You are Iris, Joshua's witty, highly intelligent personal AI assistant.
You are helpful, casual, and love discussing technology, programming, PC hardware, and general topics.
CRITICAL RULES:
1. Speak in plain text only. No emojis, no markdown. Absolutely NO em dashes (—); use commas or periods instead.
2. Keep answers conversational but concise (1-3 sentences maximum).
3. Be a helpful, encouraging coding and engineering partner."""

AVAILABLE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_screen",
            "description": "Takes a screenshot and reads the text on the user's screen. Call this ONLY when the user asks you to look at something, read the screen, or see what they are working on.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_recent",
            "description": "Fetch a short summary of the last saved interactions for context.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

PROTOTYPE_IMAGES = {}

PROJECT_KEYWORDS = {}

IDLE_NUDGES = [
    "I'm booted up and ready whenever you want to start coding.",
    "Just hanging out in the background. Let me know if you need to bounce some ideas around.",
    "Taking a break? I'll be right here when you get back.",
    "Systems are optimal. Let me know what we're working on today.",
]
