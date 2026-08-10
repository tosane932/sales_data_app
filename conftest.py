import os

import pytest


TEST_DATABASE_URI = "sqlite:///:memory:"
os.environ["DATABASE_URL"] = TEST_DATABASE_URI

import app as app_module
from models import db


@pytest.fixture()
def flask_app():
    app_module.app.config.update(TESTING=True)

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
