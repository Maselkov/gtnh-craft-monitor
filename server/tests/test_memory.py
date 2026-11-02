"""gcm/memory.py: freed memory handed back to the OS after each scan."""

import pytest

from gcm import memory
from test_network_api import ITEM, run_scan
from test_patterns_api import PLANKS_PATTERN, run_pattern_scan


@pytest.fixture()
def trims(monkeypatch):
    calls = []
    monkeypatch.setattr(memory, "_malloc_trim", lambda pad: calls.append(pad))
    return calls


def test_after_every_network_scan(client, api_headers, trims):
    run_scan(client, api_headers, [[ITEM]])
    # A rejected scan frees its buffer too.
    run_scan(client, api_headers, [[ITEM]], total_errors=1)
    assert trims == [0, 0]


def test_after_every_pattern_scan(client, api_headers, trims):
    assert run_pattern_scan(client, api_headers, [[PLANKS_PATTERN]]).get_json()["ok"]
    assert run_pattern_scan(client, api_headers, [[PLANKS_PATTERN]], total_errors=1).get_json()["rejected"]
    assert trims == [0, 0]


def test_without_malloc_trim_it_does_nothing(monkeypatch):
    monkeypatch.setattr(memory, "_malloc_trim", None)
    memory.trim()


def test_a_failing_trim_never_fails_the_scan(client, api_headers, monkeypatch):
    def broken(pad):
        raise OSError("no")

    monkeypatch.setattr(memory, "_malloc_trim", broken)
    assert run_scan(client, api_headers, [[ITEM]]).status_code == 200


def test_the_real_one_runs(monkeypatch):
    monkeypatch.setattr(memory, "_malloc_trim", memory._UNSET)
    memory.trim()  # glibc here and in the image; a no-op elsewhere
