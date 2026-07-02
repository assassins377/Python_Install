# Contributing to MInstAll

Thanks for your interest in contributing! This document outlines the process.

## Development Setup

```bash
git clone https://github.com/assassins377/Python_Install.git
cd Python_Install
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux
source .venv/bin/activate
pip install -r requirements.txt
pip install -e ".[dev]"
```

## Code Style

- Python 3.10+ with type hints where practical
- Ruff for linting and formatting: `ruff check . && ruff format .`
- Line length: 100 characters
- Follow existing patterns in the codebase

## Before Submitting

```bash
ruff check .
ruff format --check .
python -m pytest tests/ -v
```

## Commit Conventions

- `feat:` — new feature
- `fix:` — bug fix
- `docs:` — documentation
- `refactor:` — code restructuring
- `test:` — tests
- `ci:` — CI/CD changes
- `i18n:` — translations

## Adding Translations

1. Copy `i18n/en.json` as a template
2. Translate all values (keep keys unchanged)
3. Add the language to `SUPPORTED_LANGUAGES` in `i18n.py`
4. Test: `python main.py` and switch language in Settings

## Adding Programs to the Catalog

1. Use the GUI: File → Add Program, or right-click a category
2. Or edit `programs.json` directly following the schema in the add-program guide
3. Run tests: `python -m pytest tests/ -v`

## Pull Request Process

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Ensure tests pass and lint is clean
5. Open a PR against `main`
6. Describe what changed and why

## Questions?

Open an issue on GitHub: https://github.com/assassins377/Python_Install/issues
