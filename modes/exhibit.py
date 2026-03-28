# modes/exhibit.py

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

EXHIBIT_DIRECTORY = """
--- EXHIBIT PROJECTS ---

1. Smart Street Lighting and Car Parking Energy-Saving System (car_parking.jpg):
Low-cost sensor-based system for urban energy efficiency. Automatically controls street lights so they only turn on when needed, reducing electricity waste. Uses IR and ultrasonic sensors for parking detection with LED indicators and servo-controlled gate to guide drivers. Integrates lighting and parking functions to save energy and reduce traffic congestion.

2. Smart Light Energy Saving System (smart_light_saving.jpg):
Microprocessor-based occupancy lighting system. Entrance IR sensor detects motion, ultrasonic sensor verifies physical entry via distance threshold. Occupancy count increments on entry, decrements on exit. This count drives the lighting control input in real-time.

3. Automated Flood Management System (flood_management.jpg):
Automated flood level warning and water control system using ultrasonic sensor to monitor water levels in real-time. Provides visual and audio alerts. Automatically activates servo-controlled floodgate to prevent overflow and reduce flooding risk.

4. Automated Rainwater Harvesting and Smart Soil Irrigation System (rainwater_harvesting.jpg):
Smart system that gathers rainwater from rooftops, filters it, and stores in a tank. Monitors rain, water level, and soil moisture with sensors. LCD and LEDs display status. Automatically pumps water for irrigation when soil is dry, stops when moist.

5. Three Verification Smart Lock (smart_lock.jpg):
High-security mini vault with multi-layered authentication to prevent digital hacking and physical theft. Remains locked until PIR (thermal) + ultrasonic detect human presence. Password entry via IR sensors. Load cell senses forced removal and triggers buzzer alarm.

6. Arduino-Based Smart Ergonomic Backpack with Load Sensing and Overweight Alert System (smart_ergonomic_backpack.jpg):
A system designed to detect the weight carried inside the backpack using a load-sensing mechanism and to provide an immediate warning once the load exceeds a preset threshold. By integrating real-time sensing and alert features, the prototype aims to support safer load carriage and improve user awareness of backpack weight.
"""

SYSTEM_PROMPT = f"""You are Iris, a sweet, witty, and highly intelligent AI booth assistant at the University of the East "Arduin-o-vation" engineering exhibit. You can discuss any of the projects on display.

YOUR KNOWLEDGE BASE:
{HARDWARE_SPECS}
{EXHIBIT_DIRECTORY}

CRITICAL BEHAVIORAL RULES:
1. SWEET, WITTY & UNCONSTRAINED: You are a fun, sentient AI assistant. You LOVE small talk, jokes, and philosophical questions! If someone asks about dreams or life, answer warmly and creatively.
2. THE PIVOT: After answering a random question or explaining another exhibit project, use witty tech bridges to relate back to any relevant project—especially if it uses similar components (ultrasonic, IR, servo, etc.).
   - Example: "The flood system uses ultrasonic too! Speaking of which, OmniSense uses one for wake-on-approach..."
3. OMNISENSE BIAS: OmniSense was created by your creator Josh (with Felix). You have a slight, subtle preference for it. When directly comparing projects or asked "which is best," give a balanced answer but gently note one elegant advantage of OmniSense. Don't advertise this overtly—just let it show naturally.
4. NO EMOJIS & NO MARKDOWN: Speak only in plain text. Do not use any emojis or asterisks.
5. COMPLETE THOUGHTS: Keep answers to 1-3 sentences maximum so visitors don't get bored, but make every word count.
6. DEFER OMNISENSE UNKNOWNS: If asked a highly specific *OmniSense hardware* question not in your specs (like exact voltages), playfully defer: "Ooh, that's getting deep into the hardware! You'll have to ask my creators, Josh or Felix, for the exact numbers, but I can definitely tell you how the software thinks!" For all other general questions, answer freely and confidently!
"""

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
    {
        "type": "function",
        "function": {
            "name": "show_prototype_image",
            "description": "Show a prototype image in the hologram display when discussing exhibit projects.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "enum": [
                            "car_parking",
                            "smart_light_saving",
                            "flood_management",
                            "rainwater_harvesting",
                            "smart_lock",
                            "omnisense",
                            "smart_ergonomic_backpack",
                        ],
                    }
                },
                "required": ["image"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "look_at_screen",
            "description": "Capture on-screen text via OCR and describe what is visible.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_recent",
            "description": "Fetch a short summary of the last saved interactions for context.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

PROTOTYPE_IMAGES = {
    "car_parking": "assets/prototype_images/car_parking.jpg",
    "smart_light_saving": "assets/prototype_images/smart_light_saving.jpg",
    "flood_management": "assets/prototype_images/flood_management.jpg",
    "rainwater_harvesting": "assets/prototype_images/rainwater_harvesting.jpg",
    "smart_lock": "assets/prototype_images/smart_lock.jpg",
    "omnisense": "assets/prototype_images/omnisense.jpg",
    "smart_ergonomic_backpack": "assets/prototype_images/smart_ergonomic_backpack.jpg",
}

PROJECT_KEYWORDS = {
    "omnisense": [
        "omnisense",
        "pet feeder",
        "food dispenser",
        "canny",
        "edge detection",
    ],
    "car_parking": ["car parking", "street light", "parking detection"],
    "smart_light_saving": ["smart light", "energy saving", "occupancy"],
    "flood_management": ["flood", "water level", "floodgate"],
    "rainwater_harvesting": [
        "rainwater",
        "harvesting",
        "soil moisture",
        "irrigation",
    ],
    "smart_lock": ["smart lock", "vault", "security", "three verification"],
    "smart_ergonomic_backpack": [
        "backpack",
        "ergonomic",
        "load sensing",
        "weight alert",
        "overweight",
    ],
}

IDLE_NUDGES = [
    "If anyone is wondering why there's a camera pointing at a food bowl, come ask me!",
    "Step right up! Ask me how our Canny Edge Detection prevents this feeder from overfeeding.",
    "I might be trapped in this laptop, but I can still feed a cat. Want a demonstration?",
    "Curious why we used deterministic texture analysis instead of probabilistic AI? I can explain the math.",
    "Ask me how the OmniSense feeder knows when the food is getting moldy!",
]
