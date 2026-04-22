import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: marks tests that hit the live TR website (deselect with -m 'not integration')")
