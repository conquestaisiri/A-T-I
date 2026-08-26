"""Strategy playbook — curated, testable strategy families.

Each module exposes a pure `signal(context) -> Optional[Signal]` that the
AI can evaluate. Nothing here trades live until it passes PBO/DSR gates.
"""
