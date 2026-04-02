import sys
from unittest.mock import MagicMock


class StreamMock:
    def __init__(self, items):
        self.items = items

    def __iter__(self):
        return iter(self.items)


def fake_chat(**kwargs):
    messages = kwargs.get("messages", [])
    last_msg = messages[-1]["content"]

    if last_msg == "quiz me":
        return StreamMock(
            [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "start_quiz",
                                    "arguments": {"topic": "digital logic"},
                                }
                            }
                        ]
                    }
                }
            ]
        )
    elif "Generate one" in last_msg:
        return StreamMock([{"message": {"content": "What is a multiplexer?"}}])
    elif "Evaluate if they are correct" in last_msg:
        return StreamMock(
            [
                {
                    "message": {
                        "content": "Spot on! Correct. A multiplexer selects one of many inputs."
                    }
                }
            ]
        )
    elif "Quiz started successfully." in last_msg:
        return StreamMock([{"message": {"content": "Alright, let us start the quiz!"}}])
    else:
        return StreamMock([{"message": {"content": "I am not sure."}}])


ollama_mock = MagicMock()
ollama_mock.chat = fake_chat
sys.modules["ollama"] = ollama_mock

from core.iris_agent import Iris


def test():
    iris = Iris()
    iris.switch_mode("exhibit")
    vm_mock = MagicMock()
    vm_mock.abort_flag.is_set.return_value = False
    vm_mock.speak = lambda text, **kwargs: print(f"[Iris Voice]: {text}\n")
    iris.vm = vm_mock

    print("[You]: quiz me")
    response = iris.chat("quiz me", save=True, use_tools=True)

    print("--- Session Status ---")
    print(f"Iris in Quiz mode: {bool(iris.active_quiz and iris.active_quiz.active)}")

    if iris.active_quiz and iris.active_quiz.active:
        print(
            f"[You]: I think it selects one of many input signals and forwards the selected input into a single line.\n"
        )
        iris.active_quiz.answer(
            "I think it selects one of many input signals and forwards the selected input into a single line.",
            chat_fn=lambda p, save=False, use_tools=False: iris.chat(
                p, save=save, use_tools=use_tools
            ),
            speak_fn=lambda t: print(f"[Iris Voice]: {t}\n"),
        )

        print("[You]: stop quiz")
        iris.active_quiz.end(speak_fn=lambda t: print(f"[Iris Voice]: {t}\n"))


if __name__ == "__main__":
    test()
