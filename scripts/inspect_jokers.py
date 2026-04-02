import json
import sys
from pathlib import Path

# Add the project root to the sys.path to import skills
sys.path.insert(0, str(Path(__file__).parent.parent))

from skills.balatro_client import BalatroClient

def main():
    print("Fetching Balatro game state...")
    client = BalatroClient()
    
    # Use raw RPC to get the full unstructured state directly
    state = client.get_game_state()
    
    if not state:
        print("Failed to get game state. Is Balatro running with the mod?")
        return
        
    game_state = state.get("state", "UNKNOWN")
    print(f"Current Game State: {game_state}")
    
    jokers = state.get("jokers", {}).get("cards", [])
    shop = state.get("shop", {}).get("cards", [])
    
    out_path = Path(__file__).parent / "test_api_jokers.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"Balatro State Inspection\n")
        f.write(f"game_state={game_state}\n\n")
        
        f.write("=== JOKERS (CURRENTLY OWNED) ===\n")
        json.dump(jokers, f, indent=2)
        f.write("\n\n")
        
        f.write("=== SHOP (AVAILABLE TO BUY) ===\n")
        json.dump(shop, f, indent=2)
        f.write("\n")
        
    print(f"Successfully saved {len(jokers)} owned jokers and {len(shop)} shop items to {out_path}")

if __name__ == "__main__":
    main()
