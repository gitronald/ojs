import pytest


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    """Skip the API client's retry backoff so retried requests don't slow tests."""
    from ojs.api import client

    monkeypatch.setattr(client.time, "sleep", lambda _seconds: None)
