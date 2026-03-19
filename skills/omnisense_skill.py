import requests

FEEDER_OUTCOMES = {
    "success": "You just successfully activated the OmniSense pet feeder. React naturally and casually in 1-2 sentences. Check your memory — don't repeat what you said last time you fed it.",
    "timeout": "You tried to activate the OmniSense feeder but it timed out and didn't respond. React naturally — maybe confused, maybe concerned. Keep it short and in character.",
    "connection_error": "You tried to reach OmniSense but couldn't find it on the network at all. React naturally, maybe suggest checking if it's connected. 1-2 sentences, stay casual.",
    "bad_status": "The OmniSense feeder responded but gave an unexpected status. React with mild confusion in character. Keep it short."
}

def trigger(chat_fn, speak_fn):
    """
    Triggers the OmniSense feeder via HTTP GET.
    Uses a 3-second timeout so the main program doesn't freeze if the feeder is offline.
    """
    try:
        response = requests.get('http://omnisense.local/feed', timeout=3)
        if response.status_code == 200:
            outcome = FEEDER_OUTCOMES["success"]
        else:
            outcome = FEEDER_OUTCOMES["bad_status"]
    except requests.Timeout:
        outcome = FEEDER_OUTCOMES["timeout"]
    except requests.ConnectionError:
        outcome = FEEDER_OUTCOMES["connection_error"]
    except requests.RequestException:
        outcome = FEEDER_OUTCOMES["connection_error"]

    # Let the LLM generate a natural response based on outcome + memory
    iris_response = chat_fn(outcome, save=False)
    speak_fn(iris_response)
