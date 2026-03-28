import ollama
import json
import threading
import time
import random
import re
import os
from core.logger import log_iris_response, log_system
from core.vision import get_screen_text

import modes.exhibit as exhibit_mode
import modes.default as default_mode
import modes.gaming as gaming_mode
from skills import omnisense_skill

MODES = {"exhibit": exhibit_mode, "default": default_mode, "gaming": gaming_mode}

LLM_MODEL = "qwen2.5:latest"

IDLE_TIMEOUT_SECONDS = 45
HISTORY_WINDOW_USER_TURNS = 10
HISTORY_FILENAME_TEMPLATE = "history_{mode}.json"


def strip_markdown(text: str) -> str:
    """Remove markdown symbols and emojis so TTS reads clean plain text."""
    text = re.sub(r"\*{1,3}(.*?)\*{1,3}", r"\1", text)  # bold/italic
    text = re.sub(r"`{1,3}(.*?)`{1,3}", r"\1", text)  # code
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)  # headings
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # links
    text = re.sub(r"^[-*>]\s+", "", text, flags=re.MULTILINE)  # list/quote markers

    text = re.sub(r"\[.*?\]", "", text)

    text = re.sub(r"[\U0001F600-\U0001F64F]", "", text)
    text = re.sub(r"[\U0001F300-\U0001F5FF]", "", text)
    text = re.sub(r"[\U0001F680-\U0001F6FF]", "", text)
    text = re.sub(r"[\U0001F1E0-\U0001F1FF]", "", text)
    text = re.sub(r"[\U00002702-\U000027B0]", "", text)
    text = re.sub(r"[\U000024C2-\U0001F251]", "", text)
    return text.strip()


class Iris:
    def __init__(self):
        self.active_mode_name = "default"
        self.mode_data = MODES[self.active_mode_name]

        self.system_prompt = self.mode_data.SYSTEM_PROMPT
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self.history_path = self._history_path(self.active_mode_name)
        self.last_interaction_time = time.time()
        self.last_spoke_time = time.time()
        self.last_was_idle = False
        self.lock = threading.RLock()
        self.vm = None
        self.active_quiz = None
        self.last_screen_text = ""

        self._load_history()

    def _history_path(self, mode_name):
        return HISTORY_FILENAME_TEMPLATE.format(mode=mode_name)

    def _load_history(self):
        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                prior_messages = data.get("messages", [])
                stored_prompt = data.get("system_prompt")
                if stored_prompt and stored_prompt != self.system_prompt:
                    log_system(
                        "System prompt updated since last session; reusing new prompt but keeping conversation turns."
                    )

                for msg in prior_messages:
                    if msg.get("role") == "system":
                        continue
                    if "content" in msg:
                        self.messages.append(
                            {
                                "role": msg.get("role", "assistant"),
                                "content": msg["content"],
                            }
                        )
                self._trim_history()
                if len(prior_messages) > 0:
                    log_system(
                        f"Loaded {len(prior_messages)} prior turns from {self.history_path}."
                    )
        except FileNotFoundError:
            return
        except json.JSONDecodeError:
            log_system(f"History file {self.history_path} is corrupt; starting fresh.")
        except Exception as e:
            log_system(f"History load failed: {e}")

    def _to_jsonable(self, obj):
        """Coerce tool call objects and other non-serializables into JSON-safe values."""
        if isinstance(obj, dict):
            return {k: self._to_jsonable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [self._to_jsonable(v) for v in obj]
        if isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        if hasattr(obj, "model_dump"):
            try:
                return self._to_jsonable(obj.model_dump())
            except Exception:
                pass
        if hasattr(obj, "dict"):
            try:
                return self._to_jsonable(obj.dict())
            except Exception:
                pass
        return str(obj)

    def _normalize_tool_calls(self, tool_calls):
        normalized = []
        for tc in tool_calls or []:
            if isinstance(tc, dict):
                normalized.append(self._to_jsonable(tc))
                continue
            if hasattr(tc, "model_dump"):
                try:
                    normalized.append(self._to_jsonable(tc.model_dump()))
                    continue
                except Exception:
                    pass
            if hasattr(tc, "dict"):
                try:
                    normalized.append(self._to_jsonable(tc.dict()))
                    continue
                except Exception:
                    pass
            normalized.append(str(tc))
        return normalized

    def _save_history(self):
        try:
            payload = {
                "mode": self.active_mode_name,
                "system_prompt": self.system_prompt,
                "messages": [
                    self._to_jsonable(m)
                    for m in self.messages
                    if m.get("role") != "system"
                ],
            }
            with open(self.history_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=True, indent=2)
        except Exception as e:
            log_system(f"History save failed: {e}")

    def _trim_history(self):
        system_message = self.messages[0]
        recent_messages = []
        user_turns = 0

        for msg in reversed(self.messages[1:]):
            recent_messages.append(msg)
            if msg.get("role") == "user":
                user_turns += 1
            if user_turns >= HISTORY_WINDOW_USER_TURNS:
                break

        trimmed = list(reversed(recent_messages))
        self.messages = [system_message] + trimmed

    def _summarize_recent_history(self, limit=5):
        pairs = []
        pending_user = None
        for msg in self.messages:
            role = msg.get("role")
            content = msg.get("content", "").strip()
            if role == "user":
                pending_user = content
            elif role == "assistant" and pending_user:
                pairs.append((pending_user, content))
                pending_user = None

        if pending_user:
            pairs.append((pending_user, ""))

        if not pairs:
            return "No saved history yet."

        lines = []
        for idx, (user_text, assistant_text) in enumerate(pairs[-limit:], 1):
            user_snip = (user_text[:120] + "...") if len(user_text) > 120 else user_text
            assistant_snip = (
                (assistant_text[:120] + "...")
                if len(assistant_text) > 120
                else assistant_text
            )
            lines.append(f"{idx}. You: {user_snip} | Iris: {assistant_snip}")
        return "Recent interactions:\n" + "\n".join(lines)

    def _screen_text_has_changed(self, screen_text):
        normalized = screen_text.strip()
        if not normalized:
            return False
        if normalized == self.last_screen_text:
            return False
        self.last_screen_text = normalized
        return True

    def reset_idle_timer(self):
        self.last_interaction_time = time.time()

    def switch_mode(self, mode_name, avatar=None):
        """Deterministically swap the system prompt and wipe memory."""
        if mode_name not in MODES:
            return

        with self.lock:
            self.active_mode_name = mode_name
            self.mode_data = MODES[mode_name]
            self.system_prompt = self.mode_data.SYSTEM_PROMPT

            self.messages = [{"role": "system", "content": self.system_prompt}]
            self.history_path = self._history_path(mode_name)
            self.last_screen_text = ""
            self._load_history()
            self.reset_idle_timer()

            if avatar:
                if hasattr(avatar, "current_mode"):
                    avatar.current_mode.set(mode_name)
                avatar.load_watchdog_data(
                    self.mode_data.PROTOTYPE_IMAGES, self.mode_data.PROJECT_KEYWORDS
                )

            print(f"\n[System] 🔄 Core swapped to: {mode_name.upper()} MODE")

            if self.vm:
                if mode_name == "default":
                    msg = "Systems recalibrated. Ready."
                elif mode_name == "gaming":
                    msg = "Booting up the game. Don't distract me, I'm focusing!"
                else:
                    msg = "Switching to Exhibit Mode. Ready for the Arduin-o-vation presentation!"
                self.vm.speak(msg, avatar=avatar)

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

                    # Stop back-to-back nudges
                    if getattr(self, "last_was_idle", False):
                        continue

                    # --- DYNAMIC CONTEXTUAL NUDGE LOGIC ---
                    if self.active_mode_name == "default":
                        log_system("Idle trigger: Snapping screen context...")

                        screen_text = get_screen_text()

                        if screen_text and len(screen_text) > 20:
                            if not self._screen_text_has_changed(screen_text):
                                continue
                            nudge_prompt = (
                                f"I am looking at your screen right now. I see this text: '{screen_text}'. "
                                f"Make a single, casual, and witty 1-sentence comment about what I am looking at. "
                                f"Do not say 'I see' or 'I am looking at'. Just comment on the topic directly as if we are pair programming."
                            )
                        elif len(self.messages) > 3:
                            nudge_prompt = (
                                "We haven't spoken in a few minutes. Look at our recent conversation "
                                "history. Make a single, highly relevant 1-sentence comment."
                            )
                        else:
                            nudge_prompt = random.choice(self.mode_data.IDLE_NUDGES)
                    else:
                        nudge_prompt = random.choice(self.mode_data.IDLE_NUDGES)
                        log_system(
                            f"Idle trigger: Using static nudge: {nudge_prompt[:30]}..."
                        )

                    print(f"\n[Idle] Triggering unprompted speech...")

                    # Surprised expression for idle trigger, then brief pause before speaking
                    if avatar is not None:
                        avatar.set_state("surprised")

            # Execute chat outside the lock to prevent deadlocking with chat()'s own lock
            time.sleep(0.3)

            if response_text is None and "nudge_prompt" in locals():
                response_text = self.chat(
                    nudge_prompt, save=False, use_tools=False, avatar=avatar
                )

            if response_text:
                # Speaking already happened inside _speak_streamed(); just log and clean up
                print(f"Iris (Idle): {response_text}")
                with self.lock:
                    self.last_was_idle = True
                    self.messages.append(
                        {"role": "assistant", "content": response_text}
                    )
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
            # --- ABORT CHECK ---
            if self.vm and self.vm.abort_flag.is_set():
                print("\n[Iris] Generation aborted by user interruption.")
                break

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
        if self.vm:
            self.vm.abort_flag.clear()
        self.last_was_idle = False  # Reset idle toggle when user speaks

        temp_msg = {"role": "user", "content": user_text}
        self.messages.append(temp_msg)

        # Use active mode's tools; idle loop overrides with empty list via use_tools=False
        tools = self.mode_data.AVAILABLE_TOOLS if use_tools else []
        prototype_images = getattr(self.mode_data, "PROTOTYPE_IMAGES", {})

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

        # ── Fallback: Detect tool name written as text ──────────────────────────
        if not tool_calls:
            import re

            # Match patterns like [show_prototype_image] or show_prototype_image(
            tool_text_pattern = r"\[?show_prototype_image\]?"
            match = re.search(tool_text_pattern, content, re.IGNORECASE)
            if match:
                # Try to extract which image
                image_key = None
                for key in prototype_images:
                    if (
                        key.replace("_", " ") in content.lower()
                        or key in content.lower()
                    ):
                        image_key = key
                        break
                if not image_key:
                    # Default to omnisense for generic mentions
                    image_key = "omnisense"  # fallback
                tool_calls = [
                    {
                        "function": {
                            "name": "show_prototype_image",
                            "arguments": {"image": image_key},
                        }
                    }
                ]

        # Normalize tool calls into plain dicts/lists so they are safe for history + reuse
        tool_calls = self._normalize_tool_calls(tool_calls)

        # ── Tool call handling ────────────────────────────────────────────────
        if tool_calls:
            active_tool_names = {
                t["function"]["name"] for t in self.mode_data.AVAILABLE_TOOLS
            }
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
                elif function_name == "show_prototype_image":
                    image_key = (
                        tool_call.get("function", {}).get("arguments", {}).get("image")
                    )
                    if avatar:
                        avatar.show_prototype(image_key, prototype_images)
                    self.messages.append(tool_message)
                    self.messages.append(
                        {
                            "role": "tool",
                            "content": f"Prototype image '{image_key}' displayed.",
                            "name": "show_prototype_image",
                        }
                    )
                elif function_name == "remember_recent":
                    summary = self._summarize_recent_history()
                    self.messages.append(tool_message)
                    self.messages.append(
                        {
                            "role": "tool",
                            "content": summary,
                            "name": "remember_recent",
                        }
                    )
                elif function_name == "look_at_screen":
                    screen_text = get_screen_text()
                    if screen_text:
                        self.last_screen_text = screen_text.strip()
                        tool_content = f"Screen text: {screen_text}"
                    else:
                        tool_content = "Screen text unavailable right now."

                    self.messages.append(tool_message)
                    self.messages.append(
                        {
                            "role": "tool",
                            "content": tool_content,
                            "name": "look_at_screen",
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

        if save:
            self._trim_history()
            self._save_history()
        else:
            try:
                self.messages.remove(temp_msg)
            except ValueError:
                pass

        return content
