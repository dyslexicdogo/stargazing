"""Structural checks for HACS packaging (Phase 12).

HACS (category "integration") copies only custom_components/<domain> out
of the repository, so everything the integration needs at runtime must
live inside that folder, and hacs.json / manifest.json must carry the
metadata HACS and HA validate against. These tests keep a future
refactor from silently breaking installability.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "custom_components" / "stargazing"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def test_hacs_json_is_valid_and_complete():
    data = _load_json(REPO_ROOT / "hacs.json")
    assert data["name"] == "Stargazing"
    assert data["render_readme"] is True


def test_manifest_carries_hacs_required_metadata():
    data = _load_json(PACKAGE / "manifest.json")
    for key in (
        "domain",
        "name",
        "documentation",
        "issue_tracker",
        "codeowners",
        "iot_class",
        "version",
        "config_flow",
    ):
        assert data.get(key), f"manifest.json missing/empty required key: {key}"
    assert data["domain"] == "stargazing"
    repo = "https://github.com/dyslexicdogo/stargazing"
    assert data["documentation"].startswith(repo)
    assert data["issue_tracker"].startswith(repo)


def test_runtime_assets_live_inside_the_package():
    # HACS ships only the package folder -- anything outside it would
    # never reach an install.
    assert (PACKAGE / "de421.bsp").is_file()
    assert (PACKAGE / "translations" / "en.json").is_file()


def test_every_registered_card_ships_in_www():
    from custom_components.stargazing.const import CARD_RESOURCES

    assert CARD_RESOURCES, "no cards registered?"
    for filename, url_path in CARD_RESOURCES.items():
        assert (PACKAGE / "www" / filename).is_file(), (
            f"{url_path} is registered but www/{filename} does not exist"
        )


def test_license_file_exists():
    assert (REPO_ROOT / "LICENSE").is_file()
