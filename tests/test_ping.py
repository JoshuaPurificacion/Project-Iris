import requests

# coder/balatrobot defaults to port 12346
PORT = 12346
BASE_URL = f"http://127.0.0.1:{PORT}"

print(f"Pinging Balatro API at {BASE_URL}...")

try:
    # We will hit the gamestate endpoint to ensure it is actually parsing game data
    response = requests.get(f"{BASE_URL}/gamestate", timeout=3.0)
    response.raise_for_status()
    
    data = response.json()
    print("\nSuccess! The API is alive and responding.")
    print(f"Current Screen: {data.get('current_screen', 'Unknown')}")
    print(f"Bankroll: ${data.get('bankroll', 0)}")
    print("Iris is cleared to connect.")
    
except requests.exceptions.ConnectionError:
    print("\nConnection Refused.")
    print("Troubleshooting steps:")
    print("1. Is Balatro actually running?")
    print("2. Did the Lovely console pop up alongside the game?")
    print("3. Is 'BB' listed in the in-game Mods menu?")
except Exception as e:
    print(f"\nUnexpected error: {e}")