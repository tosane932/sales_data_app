import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup
from flask import g
from sqlalchemy.exc import SQLAlchemyError

import app as app_module
import material_orders as material_orders_module
from models import Dataset, MaterialOrderItem, db


NOW = datetime.datetime(2026, 9, 13, 3, 0, tzinfo=datetime.timezone.utc)


def _create_guest_dataset(*, hour_offset=0):
    now = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(minutes=hour_offset)
    )
    dataset = Dataset(
        kind="guest",
        system_key=None,
        created_at=now,
        last_activity_at=now,
        absolute_expires_at=now + datetime.timedelta(hours=2),
    )
    db.session.add(dataset)
    db.session.commit()
    return dataset


def _guest_client(flask_app, dataset):
    test_client = flask_app.test_client()
    with test_client.session_transaction() as session_data:
        session_data["_user_id"] = f"guest:{dataset.id}"
        session_data["_fresh"] = True
    return test_client


def _create_item(
    dataset,
    *,
    name,
    quantity_text=None,
    memo=None,
    is_completed=False,
    created_at=NOW,
):
    item = MaterialOrderItem(
        dataset=dataset,
        name=name,
        quantity_text=quantity_text,
        memo=memo,
        is_completed=is_completed,
        created_at=created_at,
        completed_at=NOW if is_completed else None,
    )
    db.session.add(item)
    db.session.commit()
    return item


def _post_with_csrf(client, csrf_token, path, data=None):
    payload = dict(data or {})
    payload["csrf_token"] = csrf_token(client, "/material-orders")
    return client.post(path, data=payload, follow_redirects=False)


def _tamper_csrf_token(token):
    replacement = "A" if token[0] != "A" else "B"
    return replacement + token[1:]


def _item_snapshot():
    return [
        (
            item.id,
            item.dataset_id,
            item.name,
            item.quantity_text,
            item.memo,
            item.is_completed,
            item.created_at,
            item.completed_at,
        )
        for item in MaterialOrderItem.query.order_by(MaterialOrderItem.id)
    ]


def test_admin_can_view_empty_material_order_list(
    authenticated_client,
    admin_dataset,
):
    response = authenticated_client.get("/material-orders")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "材料発注リスト" in html
    assert "未完了 0件" in html
    assert "完了済み 0件" in html
    assert "利用中：管理者" in html
    assert '<label for="material-name">' in html
    assert '<label for="material-quantity">' in html
    assert '<label for="material-memo">' in html


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {"name": "強力粉"},
            ("強力粉", None, None),
        ),
        (
            {"name": "バター", "quantity_text": "5個"},
            ("バター", "5個", None),
        ),
        (
            {"name": "牛乳", "memo": "冷蔵品"},
            ("牛乳", None, "冷蔵品"),
        ),
        (
            {
                "name": "  卵  ",
                "quantity_text": "  3パック  ",
                "memo": "  スーパーで購入  ",
            },
            ("卵", "3パック", "スーパーで購入"),
        ),
        (
            {
                "name": "塩",
                "quantity_text": "   ",
                "memo": "   ",
            },
            ("塩", None, None),
        ),
    ],
)
def test_create_material_order_item_normalizes_valid_input(
    authenticated_client,
    admin_dataset,
    csrf_token,
    payload,
    expected,
):
    response = _post_with_csrf(
        authenticated_client,
        csrf_token,
        "/material-orders",
        payload,
    )

    assert response.status_code == 303
    item = MaterialOrderItem.query.one()
    assert item.dataset_id == admin_dataset.id
    assert (item.name, item.quantity_text, item.memo) == expected
    assert item.is_completed is False
    assert item.completed_at is None


def test_same_material_name_can_be_added_more_than_once(
    authenticated_client,
    admin_dataset,
    csrf_token,
):
    for _ in range(2):
        response = _post_with_csrf(
            authenticated_client,
            csrf_token,
            "/material-orders",
            {"name": "強力粉"},
        )
        assert response.status_code == 303

    assert MaterialOrderItem.query.filter_by(
        dataset_id=admin_dataset.id,
        name="強力粉",
    ).count() == 2


def test_list_orders_incomplete_before_completed_and_newest_first(
    authenticated_client,
    admin_dataset,
):
    _create_item(
        admin_dataset,
        name="INCOMPLETE_OLD",
        created_at=NOW,
    )
    _create_item(
        admin_dataset,
        name="COMPLETED_OLD",
        is_completed=True,
        created_at=NOW + datetime.timedelta(minutes=2),
    )
    _create_item(
        admin_dataset,
        name="INCOMPLETE_NEW",
        created_at=NOW + datetime.timedelta(minutes=1),
    )
    _create_item(
        admin_dataset,
        name="COMPLETED_NEW",
        is_completed=True,
        created_at=NOW + datetime.timedelta(minutes=3),
    )

    response = authenticated_client.get("/material-orders")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert html.index("INCOMPLETE_NEW") < html.index("INCOMPLETE_OLD")
    assert html.index("INCOMPLETE_OLD") < html.index("COMPLETED_NEW")
    assert html.index("COMPLETED_NEW") < html.index("COMPLETED_OLD")


def test_completion_posts_are_explicit_and_idempotent(
    authenticated_client,
    admin_dataset,
    csrf_token,
    monkeypatch,
):
    item = _create_item(admin_dataset, name="バター")
    item_id = item.id
    first_completed_at = NOW + datetime.timedelta(minutes=5)
    monkeypatch.setattr(
        material_orders_module,
        "utc_now",
        lambda: first_completed_at,
    )
    path = f"/material-orders/{item_id}/completion"

    first_response = _post_with_csrf(
        authenticated_client,
        csrf_token,
        path,
        {"completed": "1"},
    )
    db.session.expire_all()
    completed_item = db.session.get(MaterialOrderItem, item_id)

    assert first_response.status_code == 303
    assert completed_item.is_completed is True
    assert app_module._as_utc(completed_item.completed_at) == first_completed_at

    monkeypatch.setattr(
        material_orders_module,
        "utc_now",
        lambda: first_completed_at + datetime.timedelta(hours=1),
    )
    second_response = _post_with_csrf(
        authenticated_client,
        csrf_token,
        path,
        {"completed": "1"},
    )
    db.session.expire_all()
    completed_again = db.session.get(MaterialOrderItem, item_id)

    assert second_response.status_code == 303
    assert completed_again.is_completed is True
    assert app_module._as_utc(completed_again.completed_at) == first_completed_at

    for _ in range(2):
        response = _post_with_csrf(
            authenticated_client,
            csrf_token,
            path,
            {"completed": "0"},
        )
        assert response.status_code == 303

    db.session.expire_all()
    reopened_item = db.session.get(MaterialOrderItem, item_id)
    assert reopened_item.is_completed is False
    assert reopened_item.completed_at is None


def test_invalid_completion_state_does_not_change_item(
    authenticated_client,
    admin_dataset,
    csrf_token,
):
    item = _create_item(admin_dataset, name="牛乳")
    before = _item_snapshot()

    response = _post_with_csrf(
        authenticated_client,
        csrf_token,
        f"/material-orders/{item.id}/completion",
        {"completed": "toggle"},
    )

    assert response.status_code == 400
    assert _item_snapshot() == before


def test_delete_removes_only_the_requested_item(
    authenticated_client,
    admin_dataset,
    csrf_token,
):
    deleted_item = _create_item(admin_dataset, name="削除対象")
    kept_item = _create_item(admin_dataset, name="保持対象")

    response = _post_with_csrf(
        authenticated_client,
        csrf_token,
        f"/material-orders/{deleted_item.id}/delete",
    )

    assert response.status_code == 303
    assert db.session.get(MaterialOrderItem, deleted_item.id) is None
    assert db.session.get(MaterialOrderItem, kept_item.id) is not None


def test_delete_route_does_not_accept_get(
    authenticated_client,
    admin_dataset,
):
    item = _create_item(admin_dataset, name="GETでは削除不可")

    response = authenticated_client.get(
        f"/material-orders/{item.id}/delete"
    )

    assert response.status_code == 405
    assert db.session.get(MaterialOrderItem, item.id) is not None


@pytest.mark.parametrize(
    ("payload", "field_name", "expected_error"),
    [
        ({"name": ""}, "name", "材料名を入力してください。"),
        ({"name": "   "}, "name", "材料名を入力してください。"),
        (
            {"name": "材" * 101},
            "name",
            "材料名は100文字以内で入力してください。",
        ),
        (
            {"name": "小麦粉", "quantity_text": "量" * 31},
            "quantity_text",
            "数量・単位は30文字以内で入力してください。",
        ),
        (
            {"name": "小麦粉", "memo": "メ" * 301},
            "memo",
            "メモは300文字以内で入力してください。",
        ),
    ],
)
def test_invalid_create_input_is_preserved_without_database_changes(
    authenticated_client,
    admin_dataset,
    csrf_token,
    payload,
    field_name,
    expected_error,
):
    _create_item(admin_dataset, name="既存材料")
    before = _item_snapshot()

    response = _post_with_csrf(
        authenticated_client,
        csrf_token,
        "/material-orders",
        payload,
    )

    assert response.status_code == 400
    assert _item_snapshot() == before
    document = BeautifulSoup(response.get_data(as_text=True), "html.parser")
    assert expected_error in document.get_text()
    field = document.select_one(f'[name="{field_name}"]')
    assert field is not None
    returned_value = field.get("value") if field.name == "input" else field.text
    assert returned_value == payload[field_name]


def test_hundredth_item_succeeds_and_hundred_first_is_rejected(
    authenticated_client,
    admin_dataset,
    csrf_token,
):
    db.session.add_all(
        [
            MaterialOrderItem(dataset=admin_dataset, name=f"既存材料{index}")
            for index in range(99)
        ]
    )
    db.session.commit()

    hundredth_response = _post_with_csrf(
        authenticated_client,
        csrf_token,
        "/material-orders",
        {"name": "100件目"},
    )
    assert hundredth_response.status_code == 303
    assert MaterialOrderItem.query.filter_by(
        dataset_id=admin_dataset.id
    ).count() == 100

    rejected_response = _post_with_csrf(
        authenticated_client,
        csrf_token,
        "/material-orders",
        {"name": "101件目"},
    )
    assert rejected_response.status_code == 400
    assert "材料発注リストは100件まで登録できます。" in (
        rejected_response.get_data(as_text=True)
    )
    assert 'value="101件目"' in rejected_response.get_data(as_text=True)
    assert MaterialOrderItem.query.filter_by(
        dataset_id=admin_dataset.id
    ).count() == 100


def test_admin_and_guests_only_see_their_own_material_order_items(
    flask_app,
    authenticated_client,
    admin_dataset,
):
    guest_a = _create_guest_dataset(hour_offset=1)
    guest_b = _create_guest_dataset(hour_offset=2)
    _create_item(admin_dataset, name="ADMIN_ONLY_MATERIAL")
    _create_item(guest_a, name="GUEST_A_ONLY_MATERIAL")
    _create_item(guest_b, name="GUEST_B_ONLY_MATERIAL")
    clients = {
        "admin": authenticated_client,
        "guest_a": _guest_client(flask_app, guest_a),
        "guest_b": _guest_client(flask_app, guest_b),
    }
    expected_names = {
        "admin": "ADMIN_ONLY_MATERIAL",
        "guest_a": "GUEST_A_ONLY_MATERIAL",
        "guest_b": "GUEST_B_ONLY_MATERIAL",
    }

    for principal, test_client in clients.items():
        g.pop("_login_user", None)
        response = test_client.get("/material-orders")
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        assert expected_names[principal] in html
        for other_principal, other_name in expected_names.items():
            if other_principal != principal:
                assert other_name not in html

    g.pop("_login_user", None)
    assert "利用中：ゲストデモ" in clients["guest_a"].get(
        "/material-orders"
    ).get_data(as_text=True)


def test_external_dataset_id_is_ignored_when_creating_item(
    flask_app,
    admin_dataset,
    csrf_token,
):
    guest_a = _create_guest_dataset(hour_offset=1)
    guest_b = _create_guest_dataset(hour_offset=2)
    guest_a_client = _guest_client(flask_app, guest_a)

    response = _post_with_csrf(
        guest_a_client,
        csrf_token,
        "/material-orders",
        {
            "name": "Guest A材料",
            "dataset_id": str(guest_b.id),
            "admin_dataset_id": str(admin_dataset.id),
        },
    )

    assert response.status_code == 303
    item = MaterialOrderItem.query.one()
    assert item.dataset_id == guest_a.id


@pytest.mark.parametrize("target_kind", ["guest_b", "admin"])
@pytest.mark.parametrize(
    ("route_name", "payload", "initially_completed"),
    [
        ("completion", {"completed": "1"}, False),
        ("completion", {"completed": "0"}, True),
        ("delete", {}, False),
    ],
)
def test_guest_cannot_modify_another_dataset_item_and_gets_same_404_as_missing(
    flask_app,
    admin_dataset,
    csrf_token,
    target_kind,
    route_name,
    payload,
    initially_completed,
):
    guest_a = _create_guest_dataset(hour_offset=1)
    guest_b = _create_guest_dataset(hour_offset=2)
    target_dataset = guest_b if target_kind == "guest_b" else admin_dataset
    target_item = _create_item(
        target_dataset,
        name=f"{target_kind}保護材料",
        is_completed=initially_completed,
    )
    guest_a_client = _guest_client(flask_app, guest_a)
    before = _item_snapshot()
    target_path = f"/material-orders/{target_item.id}/{route_name}"
    missing_path = f"/material-orders/{target_item.id + 9999}/{route_name}"

    target_response = _post_with_csrf(
        guest_a_client,
        csrf_token,
        target_path,
        payload,
    )
    missing_response = _post_with_csrf(
        guest_a_client,
        csrf_token,
        missing_path,
        payload,
    )

    assert target_response.status_code == 404
    assert missing_response.status_code == 404
    assert _item_snapshot() == before


@pytest.mark.parametrize(
    ("operation", "initially_completed"),
    [
        ("create", False),
        ("complete", False),
        ("reopen", True),
        ("delete", False),
    ],
)
def test_material_order_writes_reject_missing_and_tampered_csrf_without_changes(
    authenticated_client,
    admin_dataset,
    csrf_token,
    operation,
    initially_completed,
):
    item = _create_item(
        admin_dataset,
        name="CSRF保護材料",
        is_completed=initially_completed,
    )
    if operation == "create":
        path = "/material-orders"
        payload = {"name": "不正追加"}
    elif operation == "delete":
        path = f"/material-orders/{item.id}/delete"
        payload = {}
    else:
        path = f"/material-orders/{item.id}/completion"
        payload = {"completed": "1" if operation == "complete" else "0"}

    valid_token = csrf_token(authenticated_client, "/material-orders")
    before = _item_snapshot()

    missing_response = authenticated_client.post(path, data=payload)
    tampered_response = authenticated_client.post(
        path,
        data={**payload, "csrf_token": _tamper_csrf_token(valid_token)},
    )

    assert missing_response.status_code == 400
    assert tampered_response.status_code == 400
    assert _item_snapshot() == before


def test_material_order_user_input_is_html_escaped(
    authenticated_client,
    admin_dataset,
    csrf_token,
):
    attack = "<script>alert(1)</script>"
    response = _post_with_csrf(
        authenticated_client,
        csrf_token,
        "/material-orders",
        {"name": attack, "memo": attack},
    )
    assert response.status_code == 303

    list_response = authenticated_client.get("/material-orders")
    html = list_response.get_data(as_text=True)
    assert attack not in html
    assert html.count("&lt;script&gt;alert(1)&lt;/script&gt;") == 2


def test_material_order_template_does_not_disable_escaping_or_use_inner_html():
    template_source = (
        Path(app_module.app.root_path) / "templates" / "material_orders.html"
    ).read_text()

    assert "|safe" not in template_source
    assert "innerHTML" not in template_source


def test_create_commit_failure_rolls_back_without_new_item(
    authenticated_client,
    admin_dataset,
    csrf_token,
    monkeypatch,
):
    _create_item(admin_dataset, name="既存材料")
    token = csrf_token(authenticated_client, "/material-orders")
    before = _item_snapshot()
    real_rollback = db.session.rollback
    real_commit = db.session.commit
    rollback = Mock(wraps=real_rollback)
    monkeypatch.setattr(db.session, "rollback", rollback)

    def fail_after_flush():
        db.session.flush()
        raise SQLAlchemyError("test create failure after flush")

    monkeypatch.setattr(db.session, "commit", fail_after_flush)

    response = authenticated_client.post(
        "/material-orders",
        data={"name": "追加失敗材料", "csrf_token": token},
    )

    assert response.status_code == 500
    assert rollback.call_count == 1
    assert _item_snapshot() == before
    assert "材料を追加できませんでした。" in response.get_data(as_text=True)

    monkeypatch.setattr(db.session, "commit", real_commit)
    db.session.add(MaterialOrderItem(dataset=admin_dataset, name="再利用確認"))
    db.session.commit()
    assert MaterialOrderItem.query.filter_by(name="再利用確認").count() == 1


def test_completion_commit_failure_rolls_back_state_change(
    authenticated_client,
    admin_dataset,
    csrf_token,
    monkeypatch,
):
    item = _create_item(admin_dataset, name="完了失敗材料")
    token = csrf_token(authenticated_client, "/material-orders")
    before = _item_snapshot()
    real_rollback = db.session.rollback
    real_commit = db.session.commit
    rollback = Mock(wraps=real_rollback)
    monkeypatch.setattr(db.session, "rollback", rollback)

    def fail_after_flush():
        db.session.flush()
        raise SQLAlchemyError("test completion failure after flush")

    monkeypatch.setattr(db.session, "commit", fail_after_flush)

    response = authenticated_client.post(
        f"/material-orders/{item.id}/completion",
        data={"completed": "1", "csrf_token": token},
    )

    assert response.status_code == 500
    assert rollback.call_count == 1
    assert _item_snapshot() == before
    assert "材料の完了状態を更新できませんでした。" in (
        response.get_data(as_text=True)
    )

    monkeypatch.setattr(db.session, "commit", real_commit)
    db.session.add(MaterialOrderItem(dataset=admin_dataset, name="再利用確認"))
    db.session.commit()
    assert MaterialOrderItem.query.filter_by(name="再利用確認").count() == 1


def test_delete_commit_failure_rolls_back_deleted_item(
    authenticated_client,
    admin_dataset,
    csrf_token,
    monkeypatch,
):
    item = _create_item(admin_dataset, name="削除失敗材料")
    token = csrf_token(authenticated_client, "/material-orders")
    before = _item_snapshot()
    real_rollback = db.session.rollback
    real_commit = db.session.commit
    rollback = Mock(wraps=real_rollback)
    monkeypatch.setattr(db.session, "rollback", rollback)

    def fail_after_flush():
        db.session.flush()
        raise SQLAlchemyError("test delete failure after flush")

    monkeypatch.setattr(db.session, "commit", fail_after_flush)

    response = authenticated_client.post(
        f"/material-orders/{item.id}/delete",
        data={"csrf_token": token},
    )

    assert response.status_code == 500
    assert rollback.call_count == 1
    assert _item_snapshot() == before
    assert "材料を削除できませんでした。" in response.get_data(as_text=True)

    monkeypatch.setattr(db.session, "commit", real_commit)
    db.session.add(MaterialOrderItem(dataset=admin_dataset, name="再利用確認"))
    db.session.commit()
    assert MaterialOrderItem.query.filter_by(name="再利用確認").count() == 1


def test_anonymous_user_cannot_view_or_create_material_orders(
    client,
    admin_dataset,
    csrf_token,
):
    get_response = client.get("/material-orders", follow_redirects=False)
    login_token = csrf_token(client, "/login")
    post_response = client.post(
        "/material-orders",
        data={"name": "匿名材料", "csrf_token": login_token},
        follow_redirects=False,
    )

    assert get_response.status_code == 302
    assert post_response.status_code == 302
    assert MaterialOrderItem.query.count() == 0


def test_material_order_ui_has_two_step_delete_and_top_page_entry(
    authenticated_client,
    admin_dataset,
):
    item = _create_item(admin_dataset, name="削除確認材料")

    list_response = authenticated_client.get("/material-orders")
    document = BeautifulSoup(list_response.get_data(as_text=True), "html.parser")
    delete_form = document.select_one(
        f'details form[action="/material-orders/{item.id}/delete"]'
    )
    index_response = authenticated_client.get("/")

    assert list_response.status_code == 200
    assert document.select_one("details > summary") is not None
    assert delete_form is not None
    assert "本当に削除する" in delete_form.get_text()
    assert delete_form.select_one('input[name="csrf_token"]') is not None
    assert 'href="/material-orders"' in index_response.get_data(as_text=True)
