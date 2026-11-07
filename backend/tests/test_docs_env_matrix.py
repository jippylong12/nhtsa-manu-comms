"""docs/deploy.md lists every environment variable the code reads (JIP-1595)."""

import pathlib

from src.config import Settings

DEPLOY_DOC = pathlib.Path(__file__).resolve().parents[2] / "docs" / "deploy.md"

# Read straight from os.environ outside Settings.
EXTRA_ENV = ["FLATFILE_CACHE_DIR"]


def _env_names() -> set[str]:
    names = set()
    for name, field in Settings.model_fields.items():
        choices = getattr(field.validation_alias, "choices", None)
        if choices:
            names.update(str(c).upper() for c in choices)
        else:
            names.add(name.upper())
    names.update(EXTRA_ENV)
    return names


def test_every_environment_variable_appears_in_the_deploy_matrix():
    doc = DEPLOY_DOC.read_text()
    missing = sorted(n for n in _env_names() if f"`{n}`" not in doc)
    assert not missing, f"docs/deploy.md is missing: {missing}"
