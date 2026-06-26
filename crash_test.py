import sys
import traceback
from core.iris_agent import Iris
from core.avatar import AvatarWindow
from skills import balatro_skill

def excepthook(exc_type, exc_value, exc_traceback):
    with open("crash_data.txt", "w") as f:
        traceback.print_exception(exc_type, exc_value, exc_traceback, file=f)
    print("CRASHED. See crash_data.txt")

sys.excepthook = excepthook

print("Init Iris")
iris = Iris()
print("Init Avatar")
avatar = AvatarWindow()

print("Start balatro")
try:
    balatro_skill.start_balatro(iris, avatar)
except Exception as e:
    with open("crash_data.txt", "w") as f:
        traceback.print_exc(file=f)
    print("Caught Exception in start_balatro")

import time
print("Waiting 3s for daemon to run...")
time.sleep(3)
