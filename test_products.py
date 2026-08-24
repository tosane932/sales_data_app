import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from models import DailySales, Dataset, Product, db


def _product_snapshot():
    return [
        (
            product.id,
            product.year,
            product.month,
            product.name,
            product.price,
            product.is_active,
        )
        for product in Product.query.order_by(Product.id).all()
    ]


def _sales_snapshot():
    return [
        (sale.id, sale.product_id, sale.date, sale.quantity)
        for sale in DailySales.query.order_by(DailySales.id).all()
    ]


@pytest.fixture()
def product_records(flask_app, admin_dataset):
    existing_product = Product(
        dataset=admin_dataset,
        year=2026,
        month=8,
        name="既存商品",
        price=100,
    )
    other_product = Product(
        dataset=admin_dataset,
        year=2026,
        month=8,
        name="同月商品",
        price=200,
    )
    other_month_product = Product(
        dataset=admin_dataset,
        year=2026,
        month=7,
        name="別月商品",
        price=300,
    )
    db.session.add_all([
        existing_product,
        other_product,
        other_month_product,
    ])
    db.session.flush()
    db.session.add(
        DailySales(
            product_id=existing_product.id,
            date=datetime.date(2026, 8, 10),
            quantity=5,
        )
    )
    db.session.commit()

    return SimpleNamespace(
        existing_product_id=existing_product.id,
        other_product_id=other_product.id,
        other_month_product_id=other_month_product.id,
    )


@pytest.fixture()
def cross_dataset_product_records(flask_app, admin_dataset):
    now = datetime.datetime.now(datetime.timezone.utc)
    guest_dataset = Dataset(
        kind="guest",
        system_key=None,
        created_at=now,
        last_activity_at=now,
        absolute_expires_at=now + datetime.timedelta(hours=2),
    )
    admin_product = Product(
        dataset=admin_dataset,
        year=2026,
        month=8,
        name="管理者商品",
        price=100,
        is_active=True,
    )
    guest_product = Product(
        dataset=guest_dataset,
        year=2026,
        month=8,
        name="ゲスト商品",
        price=200,
        is_active=True,
    )
    db.session.add_all([
        guest_dataset,
        admin_product,
        guest_product,
    ])
    db.session.commit()

    return SimpleNamespace(
        admin_product_id=admin_product.id,
        guest_product_id=guest_product.id,
    )


def _valid_product_payload(product_records):
    return {
        "year": "2026",
        "month": "8",
        "product_id": [
            str(product_records.existing_product_id),
            str(product_records.other_product_id),
        ],
        "prod_name": ["既存商品", "同月商品"],
        "prod_price": ["100", "200"],
    }


def _assert_product_post_rejected_without_changes(
    client,
    product_records,
    payload,
    csrf_post,
):
    products_before = _product_snapshot()
    sales_before = _sales_snapshot()

    response = csrf_post(client, "/", payload)

    assert response.status_code == 400
    assert _product_snapshot() == products_before
    assert _sales_snapshot() == sales_before


def test_admin_product_get_excludes_guest_dataset_product(
    authenticated_client,
    cross_dataset_product_records,
):
    response = authenticated_client.get("/?year=2026&month=8")
    response_text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "管理者商品" in response_text
    assert "ゲスト商品" not in response_text


def test_admin_product_post_rejects_guest_dataset_product_id_without_changes(
    authenticated_client,
    cross_dataset_product_records,
    csrf_post,
):
    guest_product = db.session.get(
        Product,
        cross_dataset_product_records.guest_product_id,
    )
    guest_product_before = (
        guest_product.name,
        guest_product.price,
        guest_product.is_active,
    )

    response = csrf_post(
        authenticated_client,
        "/",
        {
            "year": "2026",
            "month": "8",
            "product_id": [str(guest_product.id)],
            "prod_name": ["越境更新商品"],
            "prod_price": ["999"],
        },
    )

    db.session.refresh(guest_product)
    assert (
        guest_product.name,
        guest_product.price,
        guest_product.is_active,
    ) == guest_product_before
    assert response.status_code in {400, 403}


def test_guest_a_product_post_rejects_guest_b_product_id_without_changes(
    flask_app,
    csrf_post,
):
    now = datetime.datetime.now(datetime.timezone.utc)

    guest_a_dataset = Dataset(
        kind="guest",
        system_key=None,
        created_at=now,
        last_activity_at=now,
        absolute_expires_at=now + datetime.timedelta(hours=2),
    )
    guest_b_dataset = Dataset(
        kind="guest",
        system_key=None,
        created_at=now,
        last_activity_at=now,
        absolute_expires_at=now + datetime.timedelta(hours=2),
    )

    guest_a_product = Product(
        dataset=guest_a_dataset,
        year=2026,
        month=8,
        name="Guest Aの商品",
        price=100,
        is_active=True,
    )
    guest_b_product = Product(
        dataset=guest_b_dataset,
        year=2026,
        month=8,
        name="Guest Bの商品",
        price=200,
        is_active=True,
    )

    db.session.add_all([
        guest_a_dataset,
        guest_b_dataset,
        guest_a_product,
        guest_b_product,
    ])
    db.session.commit()

    guest_a_product_before = (
        guest_a_product.name,
        guest_a_product.price,
        guest_a_product.is_active,
    )
    guest_b_product_before = (
        guest_b_product.name,
        guest_b_product.price,
        guest_b_product.is_active,
    )

    guest_a_client = flask_app.test_client()
    with guest_a_client.session_transaction() as session_data:
        session_data["_user_id"] = f"guest:{guest_a_dataset.id}"
        session_data["_fresh"] = True

    response = csrf_post(
        guest_a_client,
        "/",
        {
            "year": "2026",
            "month": "8",
            "product_id": [str(guest_b_product.id)],
            "prod_name": ["越境更新商品"],
            "prod_price": ["999"],
        },
    )

    db.session.refresh(guest_a_product)
    db.session.refresh(guest_b_product)

    assert response.status_code in {400, 403}

    assert (
        guest_a_product.name,
        guest_a_product.price,
        guest_a_product.is_active,
    ) == guest_a_product_before

    assert (
        guest_b_product.name,
        guest_b_product.price,
        guest_b_product.is_active,
    ) == guest_b_product_before


def test_admin_product_post_does_not_deactivate_guest_dataset_product(
    authenticated_client,
    cross_dataset_product_records,
    csrf_post,
):
    admin_product = db.session.get(
        Product,
        cross_dataset_product_records.admin_product_id,
    )
    guest_product = db.session.get(
        Product,
        cross_dataset_product_records.guest_product_id,
    )
    guest_product_before = (
        guest_product.name,
        guest_product.price,
        guest_product.is_active,
    )

    response = csrf_post(
        authenticated_client,
        "/",
        {
            "year": "2026",
            "month": "8",
            "product_id": [str(admin_product.id)],
            "prod_name": ["管理者更新商品"],
            "prod_price": ["150"],
        },
    )

    db.session.refresh(admin_product)
    db.session.refresh(guest_product)
    assert response.status_code == 200
    assert admin_product.name == "管理者更新商品"
    assert admin_product.price == 150
    assert admin_product.is_active is True
    assert (
        guest_product.name,
        guest_product.price,
        guest_product.is_active,
    ) == guest_product_before


def test_new_product_is_assigned_to_admin_dataset(
    authenticated_client,
    admin_dataset,
    csrf_post,
):
    response = csrf_post(
        authenticated_client,
        "/",
        {
            "year": "2026",
            "month": "8",
            "product_id": [""],
            "prod_name": ["新規商品"],
            "prod_price": ["250"],
        },
    )

    product = Product.query.filter_by(name="新規商品").one()

    assert response.status_code == 200
    assert product.dataset_id == admin_dataset.id


def test_multiple_new_products_are_assigned_to_admin_dataset(
    authenticated_client,
    admin_dataset,
    csrf_post,
):
    response = csrf_post(
        authenticated_client,
        "/",
        {
            "year": "2026",
            "month": "8",
            "product_id": ["", ""],
            "prod_name": ["新規商品A", "新規商品B"],
            "prod_price": ["250", "300"],
        },
    )

    products = Product.query.order_by(Product.name).all()

    assert response.status_code == 200
    assert [product.name for product in products] == [
        "新規商品A",
        "新規商品B",
    ]
    assert all(
        product.dataset_id == admin_dataset.id
        for product in products
    )


def test_product_post_fails_safely_when_admin_dataset_is_missing(
    authenticated_client,
    csrf_post,
):
    response = csrf_post(
        authenticated_client,
        "/",
        {
            "year": "2026",
            "month": "8",
            "product_id": [""],
            "prod_name": ["保存されない商品"],
            "prod_price": ["250"],
        },
    )

    assert response.status_code == 500
    assert response.get_data(as_text=True) == (
        "管理者データ領域が見つかりません。"
    )
    assert Product.query.count() == 0


def test_product_post_updates_existing_and_adds_new_products(
    authenticated_client,
    admin_dataset,
    product_records,
    csrf_post,
):
    payload = _valid_product_payload(product_records)
    payload["product_id"] = [
        str(product_records.existing_product_id),
        str(product_records.other_product_id),
        "",
        "",
    ]
    payload["prod_name"] = [
        "更新商品",
        "同月商品",
        "新規商品A",
        "新規商品B",
    ]
    payload["prod_price"] = ["150", "200", "0", "50"]
    sales_before = _sales_snapshot()

    response = csrf_post(authenticated_client, "/", payload)

    assert response.status_code == 200
    assert Product.query.count() == 5
    existing_product = db.session.get(
        Product,
        product_records.existing_product_id,
    )
    assert existing_product.name == "更新商品"
    assert existing_product.price == 150
    assert existing_product.is_active is True
    assert Product.query.filter_by(
        year=2026,
        month=8,
        name="新規商品A",
        price=0,
        dataset_id=admin_dataset.id,
        is_active=True,
    ).one()
    assert Product.query.filter_by(
        year=2026,
        month=8,
        name="新規商品B",
        price=50,
        dataset_id=admin_dataset.id,
        is_active=True,
    ).one()
    assert _sales_snapshot() == sales_before


def test_product_post_deactivates_missing_product_without_deleting_history(
    authenticated_client,
    product_records,
    csrf_post,
):
    product_b_id = product_records.existing_product_id
    sales_before = _sales_snapshot()
    product_b_sale_before = DailySales.query.filter_by(
        product_id=product_b_id,
    ).one()
    product_b_sale_snapshot = (
        product_b_sale_before.id,
        product_b_sale_before.product_id,
        product_b_sale_before.date,
        product_b_sale_before.quantity,
    )

    response = csrf_post(
        authenticated_client,
        "/",
        {
            "year": "2026",
            "month": "8",
            "product_id": [str(product_records.other_product_id)],
            "prod_name": ["同月商品"],
            "prod_price": ["200"],
        },
    )

    product_a = db.session.get(
        Product,
        product_records.other_product_id,
    )
    product_b = db.session.get(Product, product_b_id)
    product_b_sale_after = DailySales.query.filter_by(
        product_id=product_b_id,
    ).one()

    assert response.status_code == 200
    assert product_a.is_active is True
    assert product_b is not None
    assert product_b.id == product_b_id
    assert product_b.is_active is False
    assert (
        product_b_sale_after.id,
        product_b_sale_after.product_id,
        product_b_sale_after.date,
        product_b_sale_after.quantity,
    ) == product_b_sale_snapshot
    assert _sales_snapshot() == sales_before


def test_product_post_reactivates_same_product_without_losing_history(
    authenticated_client,
    product_records,
    csrf_post,
):
    product_id = product_records.existing_product_id
    product = db.session.get(Product, product_id)
    product.is_active = False
    db.session.commit()

    product_count_before = Product.query.count()
    sales_before = _sales_snapshot()

    response = csrf_post(
        authenticated_client,
        "/",
        {
            "year": "2026",
            "month": "8",
            "product_id": [
                str(product_id),
                str(product_records.other_product_id),
            ],
            "prod_name": ["再販売商品", "同月商品"],
            "prod_price": ["150", "200"],
        },
    )

    reactivated_product = db.session.get(Product, product_id)
    product_sale = DailySales.query.filter_by(
        product_id=product_id,
    ).one()

    assert response.status_code == 200
    assert Product.query.count() == product_count_before
    assert reactivated_product.id == product_id
    assert reactivated_product.name == "再販売商品"
    assert reactivated_product.price == 150
    assert reactivated_product.is_active is True
    assert _sales_snapshot() == sales_before
    assert product_sale.product_id == product_id


def test_product_post_rolls_back_all_changes_when_database_commit_fails(
    authenticated_client,
    product_records,
    monkeypatch,
    csrf_post,
):
    products_before = _product_snapshot()
    sales_before = _sales_snapshot()
    rollback_spy = Mock(wraps=db.session.rollback)

    monkeypatch.setattr(
        db.session,
        "commit",
        Mock(
            side_effect=SQLAlchemyError(
                "forced product commit failure"
            )
        ),
    )
    monkeypatch.setattr(db.session, "rollback", rollback_spy)

    response = csrf_post(
        authenticated_client,
        "/",
        {
            "year": "2026",
            "month": "8",
            "product_id": [
                str(product_records.existing_product_id),
                "",
            ],
            "prod_name": ["更新商品A", "新規商品C"],
            "prod_price": ["150", "300"],
        },
    )

    products_after = _product_snapshot()
    sales_after = _sales_snapshot()

    assert response.status_code == 500
    rollback_spy.assert_called_once_with()
    assert products_after == products_before
    assert sales_after == sales_before

    product_a = db.session.get(
        Product,
        product_records.existing_product_id,
    )
    product_b = db.session.get(
        Product,
        product_records.other_product_id,
    )
    assert product_a.name == "既存商品"
    assert product_a.price == 100
    assert product_a.is_active is True
    assert product_b.name == "同月商品"
    assert product_b.price == 200
    assert product_b.is_active is True
    assert Product.query.filter_by(name="新規商品C").first() is None
    assert "今月のメニュー登録が完了しました" not in response.get_data(
        as_text=True
    )


@pytest.mark.parametrize(
    "short_field",
    ["product_id", "prod_name", "prod_price"],
)
def test_product_post_rejects_mismatched_field_lengths_without_changes(
    authenticated_client,
    product_records,
    short_field,
    csrf_post,
):
    payload = _valid_product_payload(product_records)
    payload[short_field].pop()

    _assert_product_post_rejected_without_changes(
        authenticated_client,
        product_records,
        payload,
        csrf_post,
    )


def test_product_post_rejects_nonnumeric_product_id_without_changes(
    authenticated_client,
    product_records,
    csrf_post,
):
    payload = _valid_product_payload(product_records)
    payload["product_id"].append("abc")
    payload["prod_name"].append("改ざん商品")
    payload["prod_price"].append("400")

    _assert_product_post_rejected_without_changes(
        authenticated_client,
        product_records,
        payload,
        csrf_post,
    )


def test_product_post_rejects_unknown_product_id_without_changes(
    authenticated_client,
    product_records,
    csrf_post,
):
    payload = _valid_product_payload(product_records)
    payload["product_id"].append("999999")
    payload["prod_name"].append("不明商品")
    payload["prod_price"].append("400")

    _assert_product_post_rejected_without_changes(
        authenticated_client,
        product_records,
        payload,
        csrf_post,
    )


def test_product_post_rejects_product_from_other_month_without_changes(
    authenticated_client,
    product_records,
    csrf_post,
):
    payload = _valid_product_payload(product_records)
    payload["product_id"].append(
        str(product_records.other_month_product_id)
    )
    payload["prod_name"].append("別月改ざん商品")
    payload["prod_price"].append("400")

    _assert_product_post_rejected_without_changes(
        authenticated_client,
        product_records,
        payload,
        csrf_post,
    )


def test_product_post_rejects_duplicate_product_ids_without_changes(
    authenticated_client,
    product_records,
    csrf_post,
):
    payload = _valid_product_payload(product_records)
    payload["product_id"] = [
        str(product_records.existing_product_id),
        str(product_records.existing_product_id),
        str(product_records.other_product_id),
    ]
    payload["prod_name"] = ["最初の商品名", "後の商品名", "同月商品"]
    payload["prod_price"] = ["150", "175", "200"]

    _assert_product_post_rejected_without_changes(
        authenticated_client,
        product_records,
        payload,
        csrf_post,
    )


@pytest.mark.parametrize("invalid_price", ["", "abc", "-1", "1.5"])
def test_product_post_rejects_invalid_price_without_changes(
    authenticated_client,
    product_records,
    invalid_price,
    csrf_post,
):
    payload = _valid_product_payload(product_records)
    payload["prod_price"][0] = invalid_price

    _assert_product_post_rejected_without_changes(
        authenticated_client,
        product_records,
        payload,
        csrf_post,
    )


@pytest.mark.parametrize(
    ("year", "month"),
    [
        pytest.param("", "8", id="empty-year"),
        pytest.param("abc", "8", id="nonnumeric-year"),
        pytest.param("2026", "", id="empty-month"),
        pytest.param("2026", "abc", id="nonnumeric-month"),
        pytest.param("2026", "0", id="month-zero"),
        pytest.param("2026", "13", id="month-thirteen"),
    ],
)
def test_product_post_rejects_invalid_year_or_month_without_changes(
    authenticated_client,
    product_records,
    year,
    month,
    csrf_post,
):
    payload = _valid_product_payload(product_records)
    payload["year"] = year
    payload["month"] = month

    _assert_product_post_rejected_without_changes(
        authenticated_client,
        product_records,
        payload,
        csrf_post,
    )
