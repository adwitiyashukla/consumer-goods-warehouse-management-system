from pathlib import Path

import pytest
from pydantic import ValidationError

from dockhand.config import Settings, async_url, load_settings

ROOT = Path(__file__).resolve().parents[1]


def test_repo_config_loads() -> None:
    settings = load_settings(ROOT / "config.toml")
    assert settings.picking.routing == "optimal"
    assert settings.layout.pick_slots >= settings.catalog.skus
    assert settings.layout.aisle_length_m == pytest.approx(60.0)


@pytest.mark.parametrize(
    ("section", "values", "message"),
    [
        ("layout", {"depot_aisle": 99}, "depot_aisle"),
        ("schedule", {"departures": ["12:00", "08:00"]}, "increasing"),
        ("slotting", {"a_share": 0.7, "b_share": 0.5}, "a_share"),
        ("catalog", {"skus": 10_000}, "pick slots"),
        ("sim", {"order_lead_hours": [1.0, 2.0]}, "wave"),
        ("sim", {"start": "10:00", "end": "09:00"}, "before"),
        ("customers", {"lanes": 500}, "lanes"),
    ],
)
def test_bad_values_are_rejected(section: str, values: dict[str, object], message: str) -> None:
    settings = load_settings(ROOT / "config.toml")
    with pytest.raises(ValidationError, match=message):
        settings.with_overrides({section: values})


def test_unknown_keys_are_rejected() -> None:
    data = load_settings(ROOT / "config.toml").model_dump()
    data["picking"]["routign"] = "sshape"
    with pytest.raises(ValidationError):
        Settings.model_validate(data)


def test_environment_url_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://user:secret@db.example.com:5432/wms?sslmode=require")
    settings = load_settings(ROOT / "config.toml")
    assert settings.database.url == "postgresql+asyncpg://user:secret@db.example.com:5432/wms?ssl=require"


def test_async_url_keeps_asyncpg_urls() -> None:
    url = "postgresql+asyncpg://dockhand:dockhand@localhost:5432/dockhand"
    assert async_url(url) == url
