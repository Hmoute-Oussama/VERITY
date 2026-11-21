# Contributing to VERITY

Thank you for helping make AI-assisted software verification more reliable.

## Before contributing

- Search existing issues and pull requests first.
- Open an issue before large architectural changes.
- Keep changes focused and explain the user-visible behavior.
- Never submit secrets, credentials, private paths, or local evidence databases.

## Development setup

```bash
git clone https://github.com/Hmoute-Oussama/VERITY.git
cd VERITY
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\Activate.ps1     # Windows
python -m pip install -e ".[dev]"
pytest
```

## Pull requests

1. Create a focused branch.
2. Add or update tests.
3. Run `pytest`.
4. Run `python -m build`.
5. Test `verity --help` and the affected CLI workflow.
6. Open a pull request with reproduction steps and verification evidence.

## Testing rules

- Place tests in `tests/`.
- Use temporary project directories instead of the workspace root.
- Keep command execution disabled unless the behavior specifically requires it.
- Tests must not access the network or real agent configuration directories.
- Use meaningful assertions for evidence status and exit codes.

## Documentation

Documentation changes should use the existing Markdown style and must not expose
personal configuration or credentials.

## Security

Do not open public issues for suspected vulnerabilities. Follow `SECURITY.md`.
