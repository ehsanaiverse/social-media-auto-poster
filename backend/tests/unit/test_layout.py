"""Checks that the Slice 1 layout migration left the package importable."""

import importlib

import pytest

from social_media_auto_poster.platforms.simulator import Simulator


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
