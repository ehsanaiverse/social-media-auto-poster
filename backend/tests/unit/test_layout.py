"""Checks that the package is importable from its backend/ layout (Slice 1 migration, D32 move)."""

import importlib
from pathlib import Path

import pytest

import social_media_auto_poster
from social_media_auto_poster.platforms.simulator import Simulator

BACKEND_DIR = Path(__file__).resolve().parents[2]


def test_package_is_installed_from_backend_src():
    package_dir = Path(social_media_auto_poster.__file__).resolve().parent
    assert package_dir == BACKEND_DIR / "src" / "social_media_auto_poster"
    assert (BACKEND_DIR / "pyproject.toml").is_file()


@pytest.mark.parametrize(
    "module",
    [
        "social_media_auto_poster.config",
        "social_media_auto_poster.models",
        "social_media_auto_poster.graph",
        "social_media_auto_poster.ingestion",
        "social_media_auto_poster.platforms.base",
        "social_media_auto_poster.platforms.simulator",
        "social_media_auto_poster.services",
        "social_media_auto_poster.storage",
    ],
)
def test_module_imports(module):
    importlib.import_module(module)


@pytest.mark.parametrize(
    "module",
    [
        "social_media_auto_poster.publishing",
        "social_media_auto_poster.workflow",
        "social_media_auto_poster.core",
        "social_media_auto_poster.scheduling",
    ],
)
def test_old_packages_are_gone(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_simulator_result_is_marked_simulated():
    result = Simulator().publish(object())
    assert result.simulated is True
