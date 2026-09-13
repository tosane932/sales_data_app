import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from models import Dataset, MaterialOrderItem, db


NOW = datetime.datetime(2026, 9, 13, 3, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture(autouse=True)
def enable_sqlite_foreign_keys(flask_app):
    db.session.execute(text("PRAGMA foreign_keys = ON"))
    assert db.session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def _create_guest_dataset(*, expires_in_hours):
    dataset = Dataset(
        kind="guest",
        system_key=None,
        created_at=NOW,
        last_activity_at=NOW,
        absolute_expires_at=NOW + datetime.timedelta(hours=expires_in_hours),
    )
    db.session.add(dataset)
    db.session.flush()
    return dataset


def test_material_order_item_can_be_created_with_optional_fields_null(
    flask_app,
    admin_dataset,
):
    item = MaterialOrderItem(
        dataset=admin_dataset,
        name="強力粉",
    )
    db.session.add(item)
    db.session.commit()

    saved_item = db.session.get(MaterialOrderItem, item.id)
    assert saved_item is not None
    assert saved_item.dataset_id == admin_dataset.id
    assert saved_item.dataset == admin_dataset
    assert saved_item.name == "強力粉"
    assert saved_item.quantity_text is None
    assert saved_item.memo is None
    assert saved_item.is_completed is False
    assert saved_item.created_at is not None
    assert saved_item.completed_at is None


def test_material_order_items_are_independent_between_datasets(
    flask_app,
    admin_dataset,
):
    guest_a = _create_guest_dataset(expires_in_hours=2)
    guest_b = _create_guest_dataset(expires_in_hours=3)
    db.session.add_all(
        [
            MaterialOrderItem(dataset=admin_dataset, name="Admin材料"),
            MaterialOrderItem(dataset=guest_a, name="Guest A材料"),
            MaterialOrderItem(dataset=guest_b, name="Guest B材料"),
        ]
    )
    db.session.commit()

    assert {
        item.name
        for item in MaterialOrderItem.query.filter_by(
            dataset_id=admin_dataset.id
        )
    } == {"Admin材料"}
    assert {
        item.name
        for item in MaterialOrderItem.query.filter_by(dataset_id=guest_a.id)
    } == {"Guest A材料"}
    assert {
        item.name
        for item in MaterialOrderItem.query.filter_by(dataset_id=guest_b.id)
    } == {"Guest B材料"}


def test_material_order_item_name_is_required(flask_app, admin_dataset):
    db.session.add(
        MaterialOrderItem(
            dataset=admin_dataset,
            name=None,
        )
    )

    with pytest.raises(IntegrityError):
        db.session.commit()

    db.session.rollback()


@pytest.mark.parametrize(
    ("is_completed", "completed_at"),
    [
        (False, NOW),
        (True, None),
    ],
)
def test_material_order_item_completion_state_must_match_timestamp(
    flask_app,
    admin_dataset,
    is_completed,
    completed_at,
):
    db.session.add(
        MaterialOrderItem(
            dataset=admin_dataset,
            name="状態不整合材料",
            is_completed=is_completed,
            completed_at=completed_at,
        )
    )

    with pytest.raises(IntegrityError):
        db.session.commit()

    db.session.rollback()


def test_completed_material_order_item_accepts_completed_at(
    flask_app,
    admin_dataset,
):
    item = MaterialOrderItem(
        dataset=admin_dataset,
        name="購入済み材料",
        quantity_text="2袋",
        memo="予備を含む",
        is_completed=True,
        completed_at=NOW,
    )
    db.session.add(item)
    db.session.commit()

    assert item.is_completed is True
    assert item.completed_at is not None
    assert item.quantity_text == "2袋"
    assert item.memo == "予備を含む"


def test_dataset_delete_cascades_material_order_items(
    flask_app,
):
    guest = _create_guest_dataset(expires_in_hours=2)
    item = MaterialOrderItem(dataset=guest, name="削除対象材料")
    db.session.add(item)
    db.session.commit()
    guest_id = guest.id
    item_id = item.id

    assert guest.material_order_items == [item]

    db.session.delete(guest)
    db.session.commit()

    assert db.session.get(Dataset, guest_id) is None
    assert db.session.get(MaterialOrderItem, item_id) is None
