import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup
from flask import g
from sqlalchemy.exc import SQLAlchemyError

import app as app_module
from models import Dataset, ShopMemo, db


NOW = datetime.datetime(2026, 9, 14, 15, 0, tzinfo=datetime.timezone.utc)


def _create_guest_dataset(*, minute_offset=0):
    now = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        minutes=minute_offset
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


def _create_memo(dataset, *, body, deleted=False, minute_offset=0):
    created_at = NOW + datetime.timedelta(minutes=minute_offset)
    deleted_at = created_at if deleted else None
    memo = ShopMemo(
        dataset=dataset,
        body=body,
        created_at=created_at,
        updated_at=created_at,
        deleted_at=deleted_at,
    )
    db.session.add(memo)
    db.session.commit()
    return memo


def _post_with_csrf(client, csrf_token, path, data=None):
    payload = dict(data or {})
    payload["csrf_token"] = csrf_token(client, "/shop-tools/memo")
    return client.post(path, data=payload, follow_redirects=False)


def _memo_snapshot():
    return [
        (
            memo.id,
            memo.dataset_id,
            memo.body,
            memo.created_at,
            memo.updated_at,
            memo.deleted_at,
        )
        for memo in ShopMemo.query.order_by(ShopMemo.id)
    ]


def _tamper_csrf_token(token):
    replacement = "A" if token[0] != "A" else "B"
    return replacement + token[1:]


def test_admin_can_open_memo_list_and_create_trimmed_multiline_memo(
    authenticated_client,
    admin_dataset,
    csrf_token,
):
    empty_response = authenticated_client.get("/shop-tools/memo")
    empty_document = BeautifulSoup(
        empty_response.get_data(as_text=True), "html.parser"
    )

    assert empty_response.status_code == 200
    assert "メモ 0件" in empty_document.get_text()
    assert empty_document.select_one('label[for="shop-memo-body"]') is not None

    response = _post_with_csrf(
        authenticated_client,
        csrf_token,
        "/shop-tools/memo",
        {"body": "  牛乳の納品時間\n担当者へ確認  "},
    )

    assert response.status_code == 303
    assert response.headers["Location"] == "/shop-tools/memo"
    memo = ShopMemo.query.one()
    assert memo.dataset_id == admin_dataset.id
    assert memo.body == "牛乳の納品時間\n担当者へ確認"


def test_create_accepts_two_thousand_characters(
    authenticated_client,
    admin_dataset,
    csrf_token,
):
    body = "メ" * 2000

    response = _post_with_csrf(
        authenticated_client,
        csrf_token,
        "/shop-tools/memo",
        {"body": body},
    )

    assert response.status_code == 303
    assert ShopMemo.query.one().body == body


def test_oversized_memo_request_is_rejected_before_database_change(
    authenticated_client,
    admin_dataset,
    csrf_token,
):
    token = csrf_token(authenticated_client, "/shop-tools/memo")

    response = authenticated_client.post(
        "/shop-tools/memo",
        data={
            "body": "メモ",
            "padding": "x"
            * (app_module.app.config["MAX_CONTENT_LENGTH"] + 1),
            "csrf_token": token,
        },
    )

    assert response.status_code == 413
    assert ShopMemo.query.count() == 0


def test_memo_full_lifecycle_updates_active_and_trash_views(
    authenticated_client,
    admin_dataset,
    csrf_token,
):
    memo = _create_memo(admin_dataset, body="編集前")
    memo_id = memo.id

    edit_response = _post_with_csrf(
        authenticated_client,
        csrf_token,
        f"/shop-tools/memo/{memo_id}/edit",
        {"body": "編集後"},
    )
    assert edit_response.status_code == 303
    assert db.session.get(ShopMemo, memo_id).body == "編集後"

    trash_response = _post_with_csrf(
        authenticated_client,
        csrf_token,
        f"/shop-tools/memo/{memo_id}/trash",
    )
    assert trash_response.status_code == 303
    db.session.expire_all()
    trashed_memo = db.session.get(ShopMemo, memo_id)
    assert trashed_memo.deleted_at is not None
    assert trashed_memo.updated_at == trashed_memo.deleted_at
    assert "編集後" not in authenticated_client.get(
        "/shop-tools/memo"
    ).get_data(as_text=True)
    assert "編集後" in authenticated_client.get(
        "/shop-tools/memo/trash"
    ).get_data(as_text=True)

    restore_response = _post_with_csrf(
        authenticated_client,
        csrf_token,
        f"/shop-tools/memo/{memo_id}/restore",
    )
    assert restore_response.status_code == 303
    db.session.expire_all()
    assert db.session.get(ShopMemo, memo_id).deleted_at is None

    _post_with_csrf(
        authenticated_client,
        csrf_token,
        f"/shop-tools/memo/{memo_id}/trash",
    )
    delete_response = _post_with_csrf(
        authenticated_client,
        csrf_token,
        f"/shop-tools/memo/{memo_id}/delete",
    )
    assert delete_response.status_code == 303
    assert db.session.get(ShopMemo, memo_id) is None


@pytest.mark.parametrize(
    ("path", "expected", "excluded"),
    [
        ("/shop-tools/memo?q=納品", "牛乳の納品時間", "発注数を確認"),
        ("/shop-tools/memo?q=%25", "割引率%を確認", "発注数を確認"),
        ("/shop-tools/memo?q=_", "商品_A", "発注数を確認"),
        (
            "/shop-tools/memo/trash?q=削除",
            "削除済み納品メモ",
            "牛乳の納品時間",
        ),
    ],
)
def test_memo_search_is_literal_and_scoped_to_current_state(
    authenticated_client,
    admin_dataset,
    path,
    expected,
    excluded,
):
    _create_memo(admin_dataset, body="牛乳の納品時間")
    _create_memo(admin_dataset, body="発注数を確認")
    _create_memo(admin_dataset, body="割引率%を確認")
    _create_memo(admin_dataset, body="商品_A")
    _create_memo(admin_dataset, body="削除済み納品メモ", deleted=True)

    response = authenticated_client.get(path)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert expected in html
    assert excluded not in html


def test_search_ui_shows_clear_button_only_for_nonempty_query(
    authenticated_client,
    admin_dataset,
):
    empty_document = BeautifulSoup(
        authenticated_client.get("/shop-tools/memo").get_data(as_text=True),
        "html.parser",
    )
    search_document = BeautifulSoup(
        authenticated_client.get(
            "/shop-tools/memo?q=%E7%B4%8D%E5%93%81"
        ).get_data(as_text=True),
        "html.parser",
    )

    empty_clear = empty_document.select_one("#memo-search-clear")
    search_clear = search_document.select_one("#memo-search-clear")
    assert empty_clear is not None
    assert empty_clear.has_attr("hidden")
    assert search_clear is not None
    assert not search_clear.has_attr("hidden")
    assert search_clear.get("type") == "button"
    assert search_clear.get("aria-label") == "検索文字をすべて消す"
    assert search_clear.get("data-clear-url") == "/shop-tools/memo"
    assert "「納品」の検索結果：0件" in search_document.get_text()


def test_admin_and_guests_only_search_their_own_memos(
    flask_app,
    authenticated_client,
    admin_dataset,
):
    guest_a = _create_guest_dataset(minute_offset=1)
    guest_b = _create_guest_dataset(minute_offset=2)
    _create_memo(admin_dataset, body="COMMON ADMIN")
    _create_memo(guest_a, body="COMMON GUEST A")
    _create_memo(guest_b, body="COMMON GUEST B")
    clients = {
        "ADMIN": authenticated_client,
        "GUEST A": _guest_client(flask_app, guest_a),
        "GUEST B": _guest_client(flask_app, guest_b),
    }

    for expected, test_client in clients.items():
        g.pop("_login_user", None)
        html = test_client.get("/shop-tools/memo?q=COMMON").get_data(
            as_text=True
        )
        assert f"COMMON {expected}" in html
        for other in clients:
            if other != expected:
                assert f"COMMON {other}" not in html


@pytest.mark.parametrize("body", ["", "   ", "メ" * 2001])
def test_invalid_create_preserves_input_without_database_change(
    authenticated_client,
    admin_dataset,
    csrf_token,
    body,
):
    before = _memo_snapshot()

    response = _post_with_csrf(
        authenticated_client,
        csrf_token,
        "/shop-tools/memo",
        {"body": body},
    )

    assert response.status_code == 400
    assert _memo_snapshot() == before
    document = BeautifulSoup(response.get_data(as_text=True), "html.parser")
    assert document.select_one('[name="body"]').text == body


def test_hundredth_memo_succeeds_and_limit_counts_trashed_memos(
    authenticated_client,
    admin_dataset,
    csrf_token,
):
    db.session.add_all(
        [
            ShopMemo(
                dataset=admin_dataset,
                body=f"既存メモ{index}",
                deleted_at=NOW if index == 0 else None,
                created_at=NOW,
                updated_at=NOW,
            )
            for index in range(99)
        ]
    )
    db.session.commit()

    hundredth_response = _post_with_csrf(
        authenticated_client,
        csrf_token,
        "/shop-tools/memo",
        {"body": "100件目"},
    )
    assert hundredth_response.status_code == 303
    assert ShopMemo.query.count() == 100

    rejected_response = _post_with_csrf(
        authenticated_client,
        csrf_token,
        "/shop-tools/memo",
        {"body": "101件目"},
    )

    assert rejected_response.status_code == 400
    assert "メモはゴミ箱を含めて100件まで登録できます。" in (
        rejected_response.get_data(as_text=True)
    )
    assert ShopMemo.query.count() == 100


def test_search_rejects_more_than_one_hundred_characters_without_db_changes(
    authenticated_client,
    admin_dataset,
):
    _create_memo(admin_dataset, body="保持されるメモ")
    before = _memo_snapshot()

    response = authenticated_client.get(f"/shop-tools/memo?q={'検' * 101}")

    assert response.status_code == 400
    assert "検索文字は100文字以内で入力してください。" in (
        response.get_data(as_text=True)
    )
    assert _memo_snapshot() == before


def test_blank_search_displays_all_active_memos(
    authenticated_client,
    admin_dataset,
):
    _create_memo(admin_dataset, body="表示対象A")
    _create_memo(admin_dataset, body="表示対象B")

    response = authenticated_client.get("/shop-tools/memo?q=%20%20%20")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "表示対象A" in html
    assert "表示対象B" in html
    assert "検索結果" not in html


@pytest.mark.parametrize("operation", ["edit", "trash", "restore", "delete"])
def test_guest_cannot_mutate_another_dataset_memo_and_gets_same_404_as_missing(
    flask_app,
    admin_dataset,
    csrf_token,
    operation,
):
    guest = _create_guest_dataset(minute_offset=1)
    target = _create_memo(
        admin_dataset,
        body="Admin保護メモ",
        deleted=operation in {"restore", "delete"},
    )
    guest_client = _guest_client(flask_app, guest)
    before = _memo_snapshot()
    payload = {"body": "越境編集"} if operation == "edit" else {}

    target_response = _post_with_csrf(
        guest_client,
        csrf_token,
        f"/shop-tools/memo/{target.id}/{operation}",
        payload,
    )
    missing_response = _post_with_csrf(
        guest_client,
        csrf_token,
        f"/shop-tools/memo/{target.id + 9999}/{operation}",
        payload,
    )

    assert target_response.status_code == 404
    assert missing_response.status_code == 404
    assert _memo_snapshot() == before


def test_external_dataset_id_is_ignored_when_creating_memo(
    flask_app,
    admin_dataset,
    csrf_token,
):
    guest_a = _create_guest_dataset(minute_offset=1)
    guest_b = _create_guest_dataset(minute_offset=2)
    guest_client = _guest_client(flask_app, guest_a)

    response = _post_with_csrf(
        guest_client,
        csrf_token,
        "/shop-tools/memo",
        {
            "body": "Guest Aメモ",
            "dataset_id": str(guest_b.id),
            "admin_dataset_id": str(admin_dataset.id),
        },
    )

    assert response.status_code == 303
    assert ShopMemo.query.one().dataset_id == guest_a.id


@pytest.mark.parametrize(
    ("operation", "deleted"),
    [
        ("edit", False),
        ("trash", False),
        ("restore", True),
        ("delete", True),
    ],
)
def test_memo_writes_reject_missing_and_tampered_csrf_without_changes(
    authenticated_client,
    admin_dataset,
    csrf_token,
    operation,
    deleted,
):
    memo = _create_memo(admin_dataset, body="CSRF保護メモ", deleted=deleted)
    path = f"/shop-tools/memo/{memo.id}/{operation}"
    payload = {"body": "不正編集"} if operation == "edit" else {}
    valid_token = csrf_token(authenticated_client, "/shop-tools/memo")
    before = _memo_snapshot()

    missing_response = authenticated_client.post(path, data=payload)
    tampered_response = authenticated_client.post(
        path,
        data={**payload, "csrf_token": _tamper_csrf_token(valid_token)},
    )

    assert missing_response.status_code == 400
    assert tampered_response.status_code == 400
    assert _memo_snapshot() == before


def test_create_rejects_missing_and_tampered_csrf_without_changes(
    authenticated_client,
    admin_dataset,
    csrf_token,
):
    valid_token = csrf_token(authenticated_client, "/shop-tools/memo")

    missing_response = authenticated_client.post(
        "/shop-tools/memo",
        data={"body": "不正追加A"},
    )
    tampered_response = authenticated_client.post(
        "/shop-tools/memo",
        data={
            "body": "不正追加B",
            "csrf_token": _tamper_csrf_token(valid_token),
        },
    )

    assert missing_response.status_code == 400
    assert tampered_response.status_code == 400
    assert ShopMemo.query.count() == 0


@pytest.mark.parametrize("operation", ["edit", "trash", "restore", "delete"])
def test_memo_mutation_routes_do_not_accept_get(
    authenticated_client,
    admin_dataset,
    operation,
):
    memo = _create_memo(
        admin_dataset,
        body="GETでは変更不可",
        deleted=operation in {"restore", "delete"},
    )
    before = _memo_snapshot()

    response = authenticated_client.get(
        f"/shop-tools/memo/{memo.id}/{operation}"
    )

    assert response.status_code == 405
    assert _memo_snapshot() == before


@pytest.mark.parametrize(
    ("operation", "deleted"),
    [
        ("edit", True),
        ("trash", True),
        ("restore", False),
        ("delete", False),
    ],
)
def test_memo_mutations_reject_wrong_lifecycle_state(
    authenticated_client,
    admin_dataset,
    csrf_token,
    operation,
    deleted,
):
    memo = _create_memo(admin_dataset, body="状態保護メモ", deleted=deleted)
    before = _memo_snapshot()
    payload = {"body": "変更後"} if operation == "edit" else {}

    response = _post_with_csrf(
        authenticated_client,
        csrf_token,
        f"/shop-tools/memo/{memo.id}/{operation}",
        payload,
    )

    assert response.status_code == 404
    assert _memo_snapshot() == before


def test_invalid_edit_preserves_input_without_database_change(
    authenticated_client,
    admin_dataset,
    csrf_token,
):
    memo = _create_memo(admin_dataset, body="元の本文")
    before = _memo_snapshot()

    response = _post_with_csrf(
        authenticated_client,
        csrf_token,
        f"/shop-tools/memo/{memo.id}/edit",
        {"body": "   "},
    )

    assert response.status_code == 400
    assert _memo_snapshot() == before
    document = BeautifulSoup(response.get_data(as_text=True), "html.parser")
    edit_details = document.select_one("details.shop-memo-edit[open]")
    assert edit_details is not None
    assert edit_details.select_one('textarea[name="body"]').text == "   "


@pytest.mark.parametrize(
    ("operation", "deleted", "expected_message"),
    [
        ("edit", False, "メモを更新できませんでした。"),
        ("trash", False, "メモをゴミ箱へ移動できませんでした。"),
        ("restore", True, "メモを復元できませんでした。"),
        ("delete", True, "メモを完全に削除できませんでした。"),
    ],
)
def test_mutation_commit_failure_rolls_back_memo_change(
    authenticated_client,
    admin_dataset,
    csrf_token,
    monkeypatch,
    operation,
    deleted,
    expected_message,
):
    memo = _create_memo(admin_dataset, body="rollback対象", deleted=deleted)
    token = csrf_token(authenticated_client, "/shop-tools/memo")
    before = _memo_snapshot()
    real_rollback = db.session.rollback
    rollback = Mock(wraps=real_rollback)
    monkeypatch.setattr(db.session, "rollback", rollback)

    def fail_after_flush():
        db.session.flush()
        raise SQLAlchemyError("test memo mutation failure after flush")

    monkeypatch.setattr(db.session, "commit", fail_after_flush)
    payload = {
        "csrf_token": token,
        **({"body": "保存されない変更"} if operation == "edit" else {}),
    }

    response = authenticated_client.post(
        f"/shop-tools/memo/{memo.id}/{operation}",
        data=payload,
    )

    assert response.status_code == 500
    assert rollback.call_count == 1
    assert expected_message in response.get_data(as_text=True)
    assert _memo_snapshot() == before


def test_create_commit_failure_rolls_back_new_memo(
    authenticated_client,
    admin_dataset,
    csrf_token,
    monkeypatch,
):
    token = csrf_token(authenticated_client, "/shop-tools/memo")
    real_rollback = db.session.rollback
    rollback = Mock(wraps=real_rollback)
    monkeypatch.setattr(db.session, "rollback", rollback)

    def fail_after_flush():
        db.session.flush()
        raise SQLAlchemyError("test memo create failure after flush")

    monkeypatch.setattr(db.session, "commit", fail_after_flush)
    response = authenticated_client.post(
        "/shop-tools/memo",
        data={"body": "保存されないメモ", "csrf_token": token},
    )

    assert response.status_code == 500
    assert rollback.call_count == 1
    assert "メモを追加できませんでした。" in (
        response.get_data(as_text=True)
    )
    assert ShopMemo.query.count() == 0


def test_anonymous_user_cannot_view_or_create_shop_memos(
    client,
    admin_dataset,
    csrf_token,
):
    get_response = client.get("/shop-tools/memo", follow_redirects=False)
    login_token = csrf_token(client, "/login")
    post_response = client.post(
        "/shop-tools/memo",
        data={"body": "匿名メモ", "csrf_token": login_token},
        follow_redirects=False,
    )

    assert get_response.status_code == 302
    assert post_response.status_code == 302
    assert ShopMemo.query.count() == 0


def test_shop_memo_input_is_html_escaped_and_sources_avoid_inner_html(
    authenticated_client,
    admin_dataset,
    csrf_token,
):
    attack = "<script>alert(1)</script>"
    assert _post_with_csrf(
        authenticated_client,
        csrf_token,
        "/shop-tools/memo",
        {"body": attack},
    ).status_code == 303

    html = authenticated_client.get("/shop-tools/memo").get_data(as_text=True)
    assert attack not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html

    project_root = Path(app_module.app.root_path)
    for path in [
        project_root / "templates" / "shop_tools" / "memos.html",
        project_root / "templates" / "shop_tools" / "memo_trash.html",
        project_root / "static" / "shop_memos.js",
    ]:
        source = path.read_text()
        assert "|safe" not in source
        assert "innerHTML" not in source


def test_memo_ui_has_explicit_confirmations_and_accessible_controls(
    authenticated_client,
    admin_dataset,
):
    active_memo = _create_memo(admin_dataset, body="通常メモ")
    deleted_memo = _create_memo(admin_dataset, body="削除済み", deleted=True)

    active_document = BeautifulSoup(
        authenticated_client.get("/shop-tools/memo").get_data(as_text=True),
        "html.parser",
    )
    trash_document = BeautifulSoup(
        authenticated_client.get("/shop-tools/memo/trash").get_data(
            as_text=True
        ),
        "html.parser",
    )

    assert active_document.select_one(
        f'form[action="/shop-tools/memo/{active_memo.id}/edit"] '
        'input[name="csrf_token"]'
    ) is not None
    assert active_document.select_one(
        f'details form[action="/shop-tools/memo/{active_memo.id}/trash"]'
    ) is not None
    assert trash_document.select_one(
        f'form[action="/shop-tools/memo/{deleted_memo.id}/restore"]'
    ) is not None
    permanent_delete = trash_document.select_one(
        f'details form[action="/shop-tools/memo/{deleted_memo.id}/delete"]'
    )
    assert permanent_delete is not None
    assert "この操作は取り消せません" in permanent_delete.parent.get_text()

    script_source = (
        Path(app_module.app.root_path) / "static" / "shop_memos.js"
    ).read_text()
    assert 'memoSearchInput.addEventListener("input"' in script_source
    assert 'memoSearchClearButton.addEventListener("click"' in script_source
    assert 'memoSearchInput.value = ""' in script_source
    assert "window.location.replace" in script_source
