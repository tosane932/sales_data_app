import os

from sqlalchemy.engine import make_url


POSTGRESQL_TEST_DATABASE_URL_ENV = "TEST_POSTGRESQL_DATABASE_URL"
POSTGRESQL_RESET_OPT_IN_ENV = "ALLOW_POSTGRESQL_TEST_DATABASE_RESET"
EXPECTED_TEST_DATABASE_NAME = "sales_data_app_test"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def get_isolated_postgresql_test_url():
    """破壊可能な使い捨てPostgreSQL URLだけをtestへ渡す。"""
    database_url = os.environ.get(POSTGRESQL_TEST_DATABASE_URL_ENV)
    if not database_url:
        return None

    configured_url = make_url(database_url)
    if configured_url.get_backend_name() != "postgresql":
        raise RuntimeError(
            "PostgreSQL integration tests require a PostgreSQL URL."
        )
    if configured_url.host not in LOOPBACK_HOSTS:
        raise RuntimeError(
            "PostgreSQL integration tests require a loopback database host."
        )
    if configured_url.database != EXPECTED_TEST_DATABASE_NAME:
        raise RuntimeError(
            "PostgreSQL integration tests require the dedicated test "
            f"database named {EXPECTED_TEST_DATABASE_NAME}."
        )
    if os.environ.get(POSTGRESQL_RESET_OPT_IN_ENV) != "1":
        raise RuntimeError(
            "PostgreSQL integration database reset was not explicitly "
            "enabled."
        )

    return database_url
