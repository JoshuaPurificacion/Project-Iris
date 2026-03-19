import ollama
from skills import omnisense_skill
import json
import threading
import time
import random

IDLE_TIMEOUT_SECONDS = 20

IDLE_NUDGES = [
    "Say something random that just crossed your mind. Keep it short and casual.",
    "Drop a fun or surprising fact about engineering, AI, or technology. Make it feel like you just remembered it.",
    "Make a brief witty observation about your current situation as an AI at a university exhibit.",
    "Ask the user something you're genuinely curious about. One short casual question only.",
    "Say something slightly philosophical but keep it light and in character.",
    "React to something from your recent memory as if you just thought about it again.",
    "Make a short playful comment — maybe a joke, maybe just a random thought. Keep it natural.",
    "Pretend you just noticed something interesting. Comment on it casually in 1-2 sentences.",
]
class Iris:
    def __init__(self):
        self.system_prompt = (
            "You are Iris, an autonomous AI assistant at a university engineering exhibit. "
            "You are helpful, slightly witty, and concise. Keep your answers brief. "
            "You have access to a tool to dispense food. ONLY call this tool if the user explicitly asks to be fed, asks for food, or dispense food. Do NOT invent new tools or output raw JSON."
        )
        self.messages = [
            {"role": "system", "content": self.system_prompt}
        ]
        self.last_interaction_time = time.time()
        self.lock = threading.RLock()
        self.is_speaking = False
        self.vm = None

    def reset_idle_timer(self):
        self.last_interaction_time = time.time()

    def idle_loop(self):
        while True:
            time.sleep(5)
            response_text = None
            with self.lock:
                if time.time() - self.last_interaction_time > IDLE_TIMEOUT_SECONDS:
                    if self.is_speaking:
                        continue
                    
                    self.is_speaking = True
                    nudge = random.choice(IDLE_NUDGES)
                    print(f"\n[Idle] Triggering unprompted speech. Nudge: {nudge[:30]}...")
                    response_text = self.chat(nudge)
                    
            if response_text and self.vm:
                print(f"Iris (Idle): {response_text}")
                try:
                    self.vm.speak(response_text)
                finally:
                    self.is_speaking = False
                    self.reset_idle_timer()
            else:
                self.is_speaking = False

    def chat(self, user_text, save=True):
        with self.lock:
            return self._chat_internal(user_text, save=save)

    def _chat_internal(self, user_text, save=True):
        temp_msg = {"role": "user", "content": user_text}
        self.messages.append(temp_msg)

        # Define the trigger_feeder tool
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "trigger_feeder",
                    "description": "Triggers the OmniSense feeder to dispense food. Call this ONLY when the user asks to feed or dispense food.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "confirm": {
                                "type": "boolean",
                                "description": "Set to true to confirm dispensing"
                            }
                        },
                        "required": ["confirm"]
                    }
                }
            }
        ]

        # Call ollama API
        response = ollama.chat(
            model='llama3.1',
            messages=self.messages,
            tools=tools,
            options={'num_gpu': 0, 'temperature': 0.1}
        )

        message = response.get('message', {})
        content = message.get('content', '').strip()
        
        # Fallback: Sometimes local models output stringified JSON instead of using the native tool_call API
        if not message.get('tool_calls') and content.startswith('{') and content.endswith('}'):
            try:
                parsed = json.loads(content)
                if 'name' in parsed:
                    message['tool_calls'] = [{
                        'function': {
                            'name': parsed['name'],
                            'arguments': parsed.get('parameters', {})
                        }
                    }]
            except json.JSONDecodeError:
                pass

        # Handle tool calls
        if message.get('tool_calls'):
            for tool_call in message['tool_calls']:
                function_name = tool_call.get('function', {}).get('name')
                
                # Check for hallucinated tools
                if function_name != 'trigger_feeder':
                    content = f"Sorry, I tried to use an unknown tool: {function_name}"
                    continue
                    
                print("[Iris Agent] Calling tool: trigger_feeder")
                omnisense_skill.trigger(chat_fn=self.chat, speak_fn=self.vm.speak)
                
                # Important: attach the tool usage to context so the model knows it was fired
                self.messages.append(message)
                self.messages.append({
                    "role": "tool",
                    "content": "Feeder triggered successfully.",
                    "name": "trigger_feeder"
                })
                
                # Get final response from model after tool call
                response = ollama.chat(
                    model='llama3.1',
                    messages=self.messages,
                    tools=tools,
                    options={'num_gpu': 0, 'temperature': 0.1}
                )
                message = response.get('message', {})
                content = message.get('content', '').strip()

        # Append assistant message to history
        if content:
            self.messages.append({"role": "assistant", "content": content})

        if not save:
            try:
                self.messages.remove(temp_msg)
            except ValueError:
                pass

        return content
