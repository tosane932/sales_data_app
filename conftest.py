import os
from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash


TEST_DATABASE_URI = "sqlite:///:memory:"
TEST_SECRET_KEY = "test-secret-key"
TEST_ADMIN_USERNAME = "test-admin"
TEST_ADMIN_PASSWORD = "test-password"
TEST_ADMIN_PASSWORD_HASH = generate_password_hash(TEST_ADMIN_PASSWORD)
os.environ["DATABASE_URL"] = TEST_DATABASE_URI

import app as app_module
from models import db


@pytest.fixture()
def flask_app():
    app_module.app.config.update(
        TESTING=True,
        SECRET_KEY=TEST_SECRET_KEY,
        ADMIN_USERNAME=TEST_ADMIN_USERNAME,
        ADMIN_PASSWORD_HASH=TEST_ADMIN_PASSWORD_HASH,
    )

    assert app_module.app.config["SQLALCHEMY_DATABASE_URI"] == TEST_DATABASE_URI

    with app_module.app.app_context():
        assert db.engine.url.drivername == "sqlite"
        assert db.engine.url.database == ":memory:"

        db.create_all()
        yield app_module.app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture()
def admin_auth_config(flask_app):
    return SimpleNamespace(
        username=TEST_ADMIN_USERNAME,
        password=TEST_ADMIN_PASSWORD,
    )


@pytest.fixture()
def authenticated_client(flask_app):
    test_client = flask_app.test_client()
    response = test_client.post(
        "/login",
        data={
            "username": TEST_ADMIN_USERNAME,
            "password": TEST_ADMIN_PASSWORD,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    return test_client
