import requests

FEED_URL = "http://omnisense.local/feed"
FEED_TIMEOUT_SECONDS = 10

FEEDER_OUTCOMES = {
    "success": "You just successfully activated the OmniSense pet feeder. React naturally and casually in 1-2 sentences. Check your memory — don't repeat what you said last time you fed it.",
    "blocked": "You tried to activate the OmniSense pet feeder, but OmniSense blocked the feed because humidity is too high. React naturally and keep it short.",
    "timeout": "You tried to activate the OmniSense feeder but it timed out and didn't respond. React naturally — maybe confused, maybe concerned. Keep it short and in character.",
    "unclear": "You reached the OmniSense feeder, but it could not clearly confirm the feed result. React naturally and briefly mention that the result was unclear.",
    "connection_error": "You tried to reach OmniSense but couldn't find it on the network at all. React naturally, maybe suggest checking if it's connected. 1-2 sentences, stay casual.",
    "bad_status": "The OmniSense feeder responded but gave an unexpected status. React with mild confusion in character. Keep it short."
}


def trigger(chat_fn, speak_fn, avatar=None):
    """
    Trigger the OmniSense feeder via HTTP GET.
    Wait long enough for OmniSense's feed cycle to return FEED:OK, FEED:BLOCKED, or TIMEOUT.
    """
    if avatar is not None:
        try:
            avatar.show_thinking()
        except Exception:
            pass

    try:
        response = requests.get(FEED_URL, timeout=FEED_TIMEOUT_SECONDS)
        response.raise_for_status()
        feeder_result = response.text.strip()

        if feeder_result == "FEED:OK":
            outcome = FEEDER_OUTCOMES["success"]
        elif feeder_result == "FEED:BLOCKED":
            outcome = FEEDER_OUTCOMES["blocked"]
        elif feeder_result == "TIMEOUT":
            outcome = FEEDER_OUTCOMES["unclear"]
        else:
            outcome = FEEDER_OUTCOMES["bad_status"]
    except requests.Timeout:
        outcome = FEEDER_OUTCOMES["timeout"]
    except requests.ConnectionError:
        outcome = FEEDER_OUTCOMES["connection_error"]
    except requests.RequestException:
        outcome = FEEDER_OUTCOMES["connection_error"]

    iris_response = chat_fn(outcome, save=False, use_tools=False)
    speak_fn(iris_response)
