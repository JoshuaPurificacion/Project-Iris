---
description: /iris-skill Use when adding any new skill to Iris
---

Create a new skill for Project Iris at skills/{skill_name}_skill.py.

Context: Project Iris is 100% offline Python AI VTuber. Skills follow the pattern in skills/omnisense_skill.py.

The skill should:
- Accept chat_fn and speak_fn as parameters
- Generate natural LLM responses via chat_fn instead of hardcoded strings
- Wrap everything in try/except with speak_fn fallback
- Never use cloud APIs

Skill to create: [DESCRIBE SKILL HERE]