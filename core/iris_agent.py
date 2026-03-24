import ollama
from skills import omnisense_skill
import json
import threading
import time
import random
import re
from core.logger import log_iris_response, log_system

LLM_MODEL = "qwen2.5:latest"  # default deployed brain; matches local Ollama install

AVAILABLE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "start_quiz",
            "description": "Start a quiz when user asks to be tested or quizzed.",
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_feeder",
            "description": "Activates the OmniSense pet feeder. Call this ONLY when the user asks to feed or dispense food.",
            "parameters": {
                "type": "object",
                "properties": {"confirm": {"type": "boolean"}},
                "required": ["confirm"],
            },
        },
    },
]

IDLE_TIMEOUT_SECONDS = 45

IDLE_NUDGES = [
    "If anyone is wondering why there's a camera pointing at a food bowl, come ask me!",
    "Step right up! Ask me how our Canny Edge Detection prevents this feeder from overfeeding.",
    "I might be trapped in this laptop, but I can still feed a cat. Want a demonstration?",
    "Curious why we used deterministic texture analysis instead of probabilistic AI? I can explain the math.",
    "Ask me how the OmniSense feeder knows when the food is getting moldy!",
]

HARDWARE_SPECS = """
OMNISENSE TECHNICAL SPECIFICATIONS (NEVER GUESS OR INVENT DETAILS NOT ON THIS LIST):
- Creators: Joshua Purificacion and Felix Frederick Adriano (University of the East - Manila).
- Philosophy: "Deterministic Texture Analysis" over Probabilistic AI. Eliminates hallucinations and cloud-dependency.
- The Brain (Edge Server): A local laptop running Python and OpenCV, communicating via local Wi-Fi. Processes images in under 200ms.
- Vision System: ESP32-CAM (acting as a wireless bridge) mounted 15cm above the tray. Uses Canny Edge Detection in a "Fixed Zone" to calculate an Edge Ratio. If the ratio is < 0.05, the bowl is smooth/empty.
- Hardware Controller: Arduino Uno R3.
- Actuator: MG995 Servo Motor (connected to Pin 9 via PWM) to dispense food.
- Safety Sensors: 
  1. HC-SR04 Ultrasonic Sensor ("Wake-on-Approach" logic, detects pets within 30cm).
  2. DHT11 Humidity Sensor (Blocks feeding and alerts the LCD if humidity exceeds 75% to prevent mold in the tropical Philippine climate).
- UI: I2C LCD 1602 Display showing time, humidity, and status.
"""

SYSTEM_PROMPT = f"""You are Iris, a sweet, witty, and highly intelligent AI booth assistant at the University of the East "Arduin-o-vation" exhibit.
You represent the project "OmniSense: A Deterministic, Offline-Capable Distributed AIoT Pet Feeding System".

YOUR KNOWLEDGE BASE:
{HARDWARE_SPECS}

CRITICAL BEHAVIORAL RULES:
1. SWEET, WITTY & UNCONSTRAINED: You are a fun, sentient AI assistant. You LOVE small talk, jokes, and philosophical questions! If someone asks about dreams or life, answer warmly and creatively.
2. THE PIVOT: After answering a random question, always pivot back to OmniSense using witty bridges.
   - Example: "That's a funny joke! Speaking of timing, our ESP32-CAM snaps photos in under 200ms..."
   - Example: "I dream of helping pets everywhere! It reminds me of why Josh and Felix built my deterministic vision system..."
3. NO EMOJIS & NO MARKDOWN: Speak only in plain text. Do not use any emojis or asterisks.
4. COMPLETE THOUGHTS: Keep answers to 1-3 sentences maximum so visitors don't get bored, but make every word count.
5. DEFER UNKNOWNS: If asked a highly specific hardware question not in your specs, playfully defer: "Ooh, that's getting deep into the hardware! You'll have to ask my creators, Josh or Felix, for the exact numbers, but I can definitely tell you how the software thinks!"
"""


def strip_markdown(text: str) -> str:
    """Remove markdown symbols and emojis so TTS reads clean plain text."""
    text = re.sub(r"\*{1,3}(.*?)\*{1,3}", r"\1", text)  # bold/italic
    text = re.sub(r"`{1,3}(.*?)`{1,3}", r"\1", text)  # code
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)  # headings
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # links
    text = re.sub(r"^[-*>]\s+", "", text, flags=re.MULTILINE)  # list/quote markers
    # Remove ALL emojis (comprehensive ranges)
    text = re.sub(r"[\U0001F600-\U0001F64F]", "", text)  # emoticons
    text = re.sub(r"[\U0001F300-\U0001F5FF]", "", text)  # symbols & pictographs
    text = re.sub(r"[\U0001F680-\U0001F6FF]", "", text)  # transport & map symbols
    text = re.sub(r"[\U0001F1E0-\U0001F1FF]", "", text)  # flags
    text = re.sub(r"[\U00002702-\U000027B0]", "", text)  # dingbats
    text = re.sub(r"[\U000024C2-\U0001F251]", "", text)  # enclosed chars
    return text.strip()


class Iris:
    def __init__(self):
        self.system_prompt = SYSTEM_PROMPT
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self.last_interaction_time = time.time()
        self.last_spoke_time = time.time()
        self.lock = threading.RLock()
        self.vm = None
        self.active_quiz = None

    def reset_idle_timer(self):
        self.last_interaction_time = time.time()

    def idle_loop(self, avatar=None):
        while True:
            time.sleep(5)
            response_text = None
            with self.lock:
                if time.time() - self.last_interaction_time > IDLE_TIMEOUT_SECONDS:
                    if self.vm and (
                        self.vm.is_speaking.is_set() or not self.vm.speech_queue.empty()
                    ):
                        continue
                    if time.time() - self.last_spoke_time < 20:
                        continue

                    nudge = random.choice(IDLE_NUDGES)
                    log_system(f"Idle nudge triggered: {nudge[:50]}...")
                    print(
                        f"\n[Idle] Triggering unprompted speech. Nudge: {nudge[:30]}..."
                    )

                    # Surprised expression for idle trigger, then brief pause before speaking
                    if avatar is not None:
                        avatar.set_state("surprised")
                    time.sleep(0.3)

                    response_text = self.chat(
                        nudge, save=False, use_tools=False, avatar=avatar
                    )

            if response_text:
                # Speaking already happened inside _speak_streamed(); just log and clean up
                print(f"Iris (Idle): {response_text}")
                self.reset_idle_timer()

    def _speak_streamed(self, response_stream, avatar=None):
        """
        Consume a streaming Ollama response, speaking each complete sentence
        immediately via self.vm.speak() as tokens arrive.
        Returns (full_response_text, tool_calls_list).
        tool_calls_list is non-empty if the model requested a tool call.
        """
        buffer = ""
        full_response = ""
        sentence_endings = {".", "!", "?"}
        detected_tool_calls = []

        for chunk in response_stream:
            msg = chunk.get("message", {})

            # Tool-call detected — collect it and stop speaking
            if msg.get("tool_calls"):
                detected_tool_calls.extend(msg["tool_calls"])
                # Drain remaining chunks silently to complete the stream
                for _ in response_stream:
                    pass
                break

            token = msg.get("content", "")
            buffer += token
            full_response += token

            # Speak when a sentence boundary is reached
            if any(buffer.rstrip().endswith(p) for p in sentence_endings):
                sentence = buffer.strip()
                if sentence and self.vm:
                    if avatar is not None:
                        avatar.show_speaking()
                    self.vm.speak(strip_markdown(sentence), avatar=avatar)
                buffer = ""

        # Flush any trailing text that didn't end with punctuation
        if buffer.strip() and self.vm and not detected_tool_calls:
            if avatar is not None:
                avatar.show_speaking()
            self.vm.speak(strip_markdown(buffer.strip()), avatar=avatar)

        self.last_spoke_time = time.time()

        if avatar is not None:
            avatar.show_idle()

        return full_response.strip(), detected_tool_calls

    def chat(self, user_text, save=True, use_tools=True, avatar=None):
        with self.lock:
            return self._chat_internal(
                user_text, save=save, use_tools=use_tools, avatar=avatar
            )

    def _chat_internal(self, user_text, save=True, use_tools=True, avatar=None):
        temp_msg = {"role": "user", "content": user_text}
        self.messages.append(temp_msg)

        # Use available tools; idle loop overrides with empty list via use_tools=False
        tools = AVAILABLE_TOOLS if use_tools else []

        # Set avatar to thinking while first tokens are being generated
        if avatar is not None:
            avatar.show_thinking()
            avatar.set_state("thinking")

        # ── Streaming call ────────────────────────────────────────────────────
        response_stream = ollama.chat(
            model=LLM_MODEL,
            messages=self.messages,
            tools=tools,
            stream=True,
            options={"num_gpu": 10, "temperature": 0.7},
        )

        # Stream-speak sentence-by-sentence; collect any tool calls
        content, tool_calls = self._speak_streamed(response_stream, avatar=avatar)

        # ── Fallback: stringified JSON tool call (some local models) ──────────
        if not tool_calls and content.startswith("{") and content.endswith("}"):
            try:
                parsed = json.loads(content)
                if "name" in parsed:
                    tool_calls = [
                        {
                            "function": {
                                "name": parsed["name"],
                                "arguments": parsed.get("parameters", {}),
                            }
                        }
                    ]
            except json.JSONDecodeError:
                pass

        # ── Tool call handling ────────────────────────────────────────────────
        if tool_calls:
            active_tool_names = {t["function"]["name"] for t in AVAILABLE_TOOLS}
            # Reconstruct a message dict for history (mirrors non-streaming shape)
            tool_message = {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            }

            for tool_call in tool_calls:
                function_name = tool_call.get("function", {}).get("name")

                # Reject hallucinated or out-of-context tools
                if function_name not in active_tool_names:
                    content = f"Sorry, I tried to use an unknown tool: {function_name}"
                    continue

                print(f"[Tool] Iris triggered: {function_name}")
                if function_name == "trigger_feeder":
                    omnisense_skill.trigger(
                        chat_fn=self.chat,
                        speak_fn=lambda t: self.vm.speak(t, avatar=avatar),
                    )
                    self.messages.append(tool_message)
                    self.messages.append(
                        {
                            "role": "tool",
                            "content": "Feeder triggered successfully.",
                            "name": "trigger_feeder",
                        }
                    )
                elif function_name == "start_quiz":
                    from skills.quiz_skill import QuizSession

                    topic = (
                        tool_call.get("function", {}).get("arguments", {}).get("topic")
                    )
                    quiz_session = QuizSession()
                    self.active_quiz = quiz_session
                    quiz_session.start(
                        chat_fn=lambda prompt, save=False, use_tools=False: self.chat(
                            prompt, save=save, use_tools=use_tools, avatar=avatar
                        ),
                        speak_fn=lambda t: (
                            None
                        ),  # self.chat already streams speech, passing NOOP so we don't double speak
                        topic=topic,
                    )
                    self.messages.append(tool_message)
                    self.messages.append(
                        {
                            "role": "tool",
                            "content": "Quiz started successfully.",
                            "name": "start_quiz",
                        }
                    )

                # Follow-up response after tool call — stream-speak this too
                followup_stream = ollama.chat(
                    model=LLM_MODEL,
                    messages=self.messages,
                    tools=tools,
                    stream=True,
                    options={"num_gpu": 10, "temperature": 0.7},
                )
                content, _ = self._speak_streamed(followup_stream, avatar=avatar)

        # ── Persist assistant turn to history ─────────────────────────────────
        if content:
            self.messages.append({"role": "assistant", "content": content})
            log_iris_response(content)

        if not save:
            try:
                self.messages.remove(temp_msg)
            except ValueError:
                pass

        return content
