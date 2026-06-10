# Virtual Environment Management with `uv`

ALWAYS use UV for Python package and environment management

```bash
# Setup and dependencies
uv venv
uv init --bare
uv sync

# Add/remove packages - ***NEVER UPDATE pyproject.toml DIRECTLY***
uv add requests
uv add --dev pytest ruff mypy
uv remove requests

# Run commands
uv run python script.py
uv run pytest
uv run ruff check .
```
