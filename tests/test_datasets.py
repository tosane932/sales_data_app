import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from models import Dataset, db


BASE_TIME = datetime.datetime(
    2026,
    8,
    19,
    12,
    0,
    tzinfo=datetime.timezone.utc,
)


def _admin_dataset(system_key="admin"):
    return Dataset(
        kind="admin",
        system_key=system_key,
        created_at=BASE_TIME,
        last_activity_at=BASE_TIME,
        absolute_expires_at=None,
    )


def _guest_dataset(
    *,
    system_key=None,
    last_activity_at=None,
    absolute_expires_at=None,
):
    return Dataset(
        kind="guest",
        system_key=system_key,
        created_at=BASE_TIME,
        last_activity_at=last_activity_at or BASE_TIME,
        absolute_expires_at=(
            absolute_expires_at
            if absolute_expires_at is not None
            else BASE_TIME + datetime.timedelta(hours=2)
        ),
    )


def _assert_dataset_rejected(dataset, expected_error_text):
    db.session.add(dataset)

    with pytest.raises(IntegrityError) as error:
        db.session.commit()

    db.session.rollback()
    assert expected_error_text in str(error.value)


def test_admin_dataset_with_admin_system_key_can_be_created(flask_app):
    dataset = _admin_dataset()
    db.session.add(dataset)
    db.session.commit()

    saved_dataset = db.session.get(Dataset, dataset.id)

    assert saved_dataset is not None
    assert saved_dataset.kind == "admin"
    assert saved_dataset.system_key == "admin"
    assert saved_dataset.absolute_expires_at is None


def test_guest_dataset_with_valid_expiration_can_be_created(flask_app):
    dataset = _guest_dataset(
        last_activity_at=BASE_TIME + datetime.timedelta(minutes=5),
    )
    db.session.add(dataset)
    db.session.commit()

    saved_dataset = db.session.get(Dataset, dataset.id)

    assert saved_dataset is not None
    assert saved_dataset.kind == "guest"
    assert saved_dataset.system_key is None
    assert saved_dataset.last_activity_at >= saved_dataset.created_at
    assert saved_dataset.absolute_expires_at > saved_dataset.created_at


def test_admin_dataset_with_non_admin_system_key_is_rejected(flask_app):
    _assert_dataset_rejected(
        _admin_dataset(system_key="admin2"),
        "ck_datasets_system_key_by_kind",
    )


def test_guest_dataset_with_system_key_is_rejected(flask_app):
    _assert_dataset_rejected(
        _guest_dataset(system_key="guest-key"),
        "ck_datasets_system_key_by_kind",
    )


def test_second_admin_dataset_is_rejected_by_unique_constraint(flask_app):
    first_dataset = _admin_dataset()
    db.session.add(first_dataset)
    db.session.commit()

    _assert_dataset_rejected(
        _admin_dataset(),
        "UNIQUE constraint failed: datasets.system_key",
    )

    assert Dataset.query.count() == 1


def test_dataset_with_unknown_kind_is_rejected(flask_app):
    dataset = Dataset(
        kind="unknown",
        system_key=None,
        created_at=BASE_TIME,
        last_activity_at=BASE_TIME,
        absolute_expires_at=None,
    )

    _assert_dataset_rejected(dataset, "ck_datasets_kind")


def test_guest_dataset_without_absolute_expiration_is_rejected(flask_app):
    dataset = Dataset(
        kind="guest",
        system_key=None,
        created_at=BASE_TIME,
        last_activity_at=BASE_TIME,
        absolute_expires_at=None,
    )

    _assert_dataset_rejected(
        dataset,
        "ck_datasets_absolute_expiry_by_kind",
    )


def test_dataset_with_activity_before_creation_is_rejected(flask_app):
    _assert_dataset_rejected(
        _guest_dataset(
            last_activity_at=BASE_TIME - datetime.timedelta(seconds=1),
        ),
        "ck_datasets_activity_not_before_creation",
    )


def test_guest_expiration_not_after_creation_is_rejected(flask_app):
    invalid_expirations = [
        BASE_TIME,
        BASE_TIME - datetime.timedelta(seconds=1),
    ]

    for absolute_expires_at in invalid_expirations:
        _assert_dataset_rejected(
            _guest_dataset(absolute_expires_at=absolute_expires_at),
            "ck_datasets_expiry_after_creation",
        )
