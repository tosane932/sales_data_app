import pytest

from postgresql_test_utils import (
    POSTGRESQL_RESET_OPT_IN_ENV,
    POSTGRESQL_TEST_DATABASE_URL_ENV,
    get_isolated_postgresql_test_url,
)


LOOPBACK_TEST_URL = (
    "postgresql+psycopg2://test:test@127.0.0.1:5432/"
    "sales_data_app_test"
)


def _configure_test_url(monkeypatch, database_url, *, allow_reset=True):
    monkeypatch.setenv(POSTGRESQL_TEST_DATABASE_URL_ENV, database_url)
    if allow_reset:
        monkeypatch.setenv(POSTGRESQL_RESET_OPT_IN_ENV, "1")
    else:
        monkeypatch.delenv(POSTGRESQL_RESET_OPT_IN_ENV, raising=False)


def test_missing_postgresql_test_url_keeps_integration_tests_disabled(
    monkeypatch,
):
    monkeypatch.delenv(POSTGRESQL_TEST_DATABASE_URL_ENV, raising=False)
    monkeypatch.delenv(POSTGRESQL_RESET_OPT_IN_ENV, raising=False)

    assert get_isolated_postgresql_test_url() is None


def test_postgresql_test_url_rejects_non_postgresql_database(monkeypatch):
    _configure_test_url(
        monkeypatch,
        "sqlite:////tmp/sales_data_app_test.db",
    )

    with pytest.raises(RuntimeError, match="require a PostgreSQL URL"):
        get_isolated_postgresql_test_url()


def test_postgresql_test_url_rejects_non_loopback_host(monkeypatch):
    _configure_test_url(
        monkeypatch,
        "postgresql://test:test@database.example/sales_data_app_test",
    )

    with pytest.raises(RuntimeError, match="require a loopback"):
        get_isolated_postgresql_test_url()


def test_postgresql_test_url_rejects_unexpected_database_name(monkeypatch):
    _configure_test_url(
        monkeypatch,
        "postgresql://test:test@127.0.0.1/not_the_test_database",
    )

    with pytest.raises(RuntimeError, match="dedicated test database"):
        get_isolated_postgresql_test_url()


def test_postgresql_test_url_requires_explicit_reset_opt_in(monkeypatch):
    _configure_test_url(monkeypatch, LOOPBACK_TEST_URL, allow_reset=False)

    with pytest.raises(RuntimeError, match="not explicitly enabled"):
        get_isolated_postgresql_test_url()


def test_postgresql_test_url_accepts_dedicated_loopback_database(monkeypatch):
    _configure_test_url(monkeypatch, LOOPBACK_TEST_URL)

    assert get_isolated_postgresql_test_url() == LOOPBACK_TEST_URL
