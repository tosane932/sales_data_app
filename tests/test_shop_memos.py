import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from models import Dataset, ShopMemo, db


NOW = datetime.datetime(2026, 9, 14, 3, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture(autouse=True)
def enable_sqlite_foreign_keys(flask_app):
    db.session.execute(text("PRAGMA foreign_keys = ON"))
    assert db.session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def _assert_commit_rejected(memo):
    db.session.add(memo)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def _create_guest_dataset():
    dataset = Dataset(
        kind="guest",
        system_key=None,
        created_at=NOW,
        last_activity_at=NOW,
        absolute_expires_at=NOW + datetime.timedelta(hours=2),
    )
    db.session.add(dataset)
    db.session.flush()
    return dataset


def test_shop_memo_can_be_created_with_utc_timestamps_and_no_deletion(
    flask_app,
    admin_dataset,
):
    memo = ShopMemo(dataset=admin_dataset, body="牛乳の納品時間を確認する")
    db.session.add(memo)
    db.session.commit()

    saved_memo = db.session.get(ShopMemo, memo.id)
    assert saved_memo is not None
    assert saved_memo.dataset_id == admin_dataset.id
    assert saved_memo.dataset == admin_dataset
    assert admin_dataset.shop_memos == [saved_memo]
    assert saved_memo.body == "牛乳の納品時間を確認する"
    assert saved_memo.created_at is not None
    assert saved_memo.updated_at is not None
    assert saved_memo.updated_at >= saved_memo.created_at
    assert saved_memo.deleted_at is None


@pytest.mark.parametrize(
    "memo",
    [
        lambda dataset: ShopMemo(dataset_id=None, body="Datasetなし"),
        lambda dataset: ShopMemo(dataset=dataset, body=None),
    ],
)
def test_shop_memo_requires_dataset_and_body(
    flask_app,
    admin_dataset,
    memo,
):
    _assert_commit_rejected(memo(admin_dataset))


@pytest.mark.parametrize("body", ["", "   "])
def test_shop_memo_rejects_empty_or_blank_body(
    flask_app,
    admin_dataset,
    body,
):
    _assert_commit_rejected(ShopMemo(dataset=admin_dataset, body=body))


def test_shop_memo_accepts_2000_characters(
    flask_app,
    admin_dataset,
):
    memo = ShopMemo(dataset=admin_dataset, body="メ" * 2000)
    db.session.add(memo)
    db.session.commit()

    assert db.session.get(ShopMemo, memo.id).body == "メ" * 2000


def test_shop_memo_rejects_2001_characters(
    flask_app,
    admin_dataset,
):
    _assert_commit_rejected(
        ShopMemo(dataset=admin_dataset, body="メ" * 2001)
    )


def test_shop_memo_allows_duplicate_bodies(
    flask_app,
    admin_dataset,
):
    db.session.add_all(
        [
            ShopMemo(dataset=admin_dataset, body="同じ本文"),
            ShopMemo(dataset=admin_dataset, body="同じ本文"),
        ]
    )
    db.session.commit()

    assert ShopMemo.query.filter_by(
        dataset_id=admin_dataset.id,
        body="同じ本文",
    ).count() == 2


@pytest.mark.parametrize(
    ("updated_at", "deleted_at"),
    [
        (NOW - datetime.timedelta(seconds=1), None),
        (NOW, NOW - datetime.timedelta(seconds=1)),
        (NOW, NOW + datetime.timedelta(seconds=1)),
    ],
)
def test_shop_memo_timestamp_order_is_enforced(
    flask_app,
    admin_dataset,
    updated_at,
    deleted_at,
):
    _assert_commit_rejected(
        ShopMemo(
            dataset=admin_dataset,
            body="時刻制約確認",
            created_at=NOW,
            updated_at=updated_at,
            deleted_at=deleted_at,
        )
    )


def test_dataset_delete_cascades_normal_and_deleted_shop_memos(flask_app):
    guest = _create_guest_dataset()
    normal_memo = ShopMemo(dataset=guest, body="通常メモ")
    deleted_memo = ShopMemo(
        dataset=guest,
        body="ゴミ箱メモ",
        created_at=NOW,
        updated_at=NOW + datetime.timedelta(minutes=1),
        deleted_at=NOW + datetime.timedelta(minutes=1),
    )
    db.session.add_all([normal_memo, deleted_memo])
    db.session.commit()
    guest_id = guest.id
    memo_ids = [normal_memo.id, deleted_memo.id]

    assert guest.shop_memos == [normal_memo, deleted_memo]

    db.session.delete(guest)
    db.session.commit()

    assert db.session.get(Dataset, guest_id) is None
    assert all(db.session.get(ShopMemo, memo_id) is None for memo_id in memo_ids)
