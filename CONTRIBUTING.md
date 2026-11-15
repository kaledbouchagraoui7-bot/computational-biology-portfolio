# Contributing

Thank you for your interest in contributing!

## Development Setup

1. Clone the repository and create a Python 3.10+ virtual environment.
2. Install dependencies (`pip install -r requirements.txt` if present, plus `pytest` and `ruff`).
3. Run the test suite before submitting a pull request:
   ```bash
   PYTHONPATH=. pytest -v
   ```

## Standards

- Keep all functions focused, deterministic, and covered by unit tests.
- Never commit API keys, credentials, or patient/protected health information (PHI).
