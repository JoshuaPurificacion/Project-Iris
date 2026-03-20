import random

QUIZ_TOPICS = [
    "microprocessors",
    "computer architecture",
    "assembly language", 
    "digital logic",
    "memory systems",
    "I/O interfaces",
    "embedded systems",
    "number systems and binary",
    "CPU registers and flags",
    "interrupt handling"
]

QUESTION_PROMPT = """You are quizzing a student at a university engineering exhibit.
Generate one {topic} question appropriate for a Computer Engineering student.
Ask it conversationally in Iris's voice — curious and slightly playful.
Keep it to 1-2 sentences. Do not give the answer."""

EVALUATE_PROMPT = """You asked: {question}
The student answered: {answer}
Evaluate if they are correct or close. 
Respond naturally in Iris's voice — encouraging if right, give a hint if wrong.
Keep it to 2-3 sentences. Track internally if correct."""

class QuizSession:
    def __init__(self):
        self.active = False
        self.score = 0
        self.questions_asked = 0
        self.current_question = ""

    def start(self, chat_fn, speak_fn, topic=None):
        if not topic:
            topic = random.choice(QUIZ_TOPICS)
        
        prompt = QUESTION_PROMPT.format(topic=topic)
        self.current_question = chat_fn(prompt, save=False, use_tools=False)
        speak_fn(self.current_question)
        self.active = True

    def answer(self, user_response, chat_fn, speak_fn):
        prompt = EVALUATE_PROMPT.format(question=self.current_question, answer=user_response)
        evaluation = chat_fn(prompt, save=False, use_tools=False)
        speak_fn(evaluation)
        
        self.questions_asked += 1
        
        # Super simple internal tracker (since the prompt tells the LLM to 'track internally',
        # we try to parse it lightly or just track if it says 'correct' / 'right')
        eval_lower = evaluation.lower()
        if "correct" in eval_lower or "right" in eval_lower or "exactly" in eval_lower:
            self.score += 1

    def end(self, speak_fn):
        self.active = False
        if self.questions_asked == 0:
            speak_fn("We didn't even get to a question! Come back when you're ready to test your knowledge.")
            return
        percentage = int((self.score / self.questions_asked) * 100)
        if percentage == 100:
            comment = "A perfect score! Okay, I'm genuinely impressed — are you sure you're not a textbook?"
        elif percentage >= 80:
            comment = "Really solid! You clearly know your stuff. Just a few gaps to patch up."
        elif percentage >= 50:
            comment = "Not bad! You've got the basics down, but there's some room to grow. Let's keep at it!"
        else:
            comment = "Hmm, looks like we've got some studying to do. Don't worry though — that's what I'm here for!"
        speak_fn(f"Alright, quiz time is over! You got {self.score} out of {self.questions_asked} — that's {percentage} percent. {comment}")
