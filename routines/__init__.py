"""
Routines - Auto-discoverable Python scripts with Pydantic configuration.

Each routine is a Python file with:
- Config: Pydantic BaseModel (docstring = menu description)
- run(config, context) -> str: Async function
"""
