import datetime
from types import SimpleNamespace

import pytest

from models import DailySales, Product, db


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
def product_records(flask_app):
    existing_product = Product(
        year=2026,
        month=8,
        name="既存商品",
        price=100,
    )
    other_product = Product(
        year=2026,
        month=8,
        name="同月商品",
        price=200,
    )
    other_month_product = Product(
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
):
    products_before = _product_snapshot()
    sales_before = _sales_snapshot()

    response = client.post("/", data=payload)

    assert response.status_code == 400
    assert _product_snapshot() == products_before
    assert _sales_snapshot() == sales_before


def test_product_post_updates_existing_and_adds_new_products(
    client,
    product_records,
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

    response = client.post("/", data=payload)

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
        is_active=True,
    ).one()
    assert Product.query.filter_by(
        year=2026,
        month=8,
        name="新規商品B",
        price=50,
        is_active=True,
    ).one()
    assert _sales_snapshot() == sales_before


@pytest.mark.parametrize(
    "short_field",
    ["product_id", "prod_name", "prod_price"],
)
def test_product_post_rejects_mismatched_field_lengths_without_changes(
    client,
    product_records,
    short_field,
):
    payload = _valid_product_payload(product_records)
    payload[short_field].pop()

    _assert_product_post_rejected_without_changes(
        client,
        product_records,
        payload,
    )


def test_product_post_rejects_nonnumeric_product_id_without_changes(
    client,
    product_records,
):
    payload = _valid_product_payload(product_records)
    payload["product_id"].append("abc")
    payload["prod_name"].append("改ざん商品")
    payload["prod_price"].append("400")

    _assert_product_post_rejected_without_changes(
        client,
        product_records,
        payload,
    )


def test_product_post_rejects_unknown_product_id_without_changes(
    client,
    product_records,
):
    payload = _valid_product_payload(product_records)
    payload["product_id"].append("999999")
    payload["prod_name"].append("不明商品")
    payload["prod_price"].append("400")

    _assert_product_post_rejected_without_changes(
        client,
        product_records,
        payload,
    )


def test_product_post_rejects_product_from_other_month_without_changes(
    client,
    product_records,
):
    payload = _valid_product_payload(product_records)
    payload["product_id"].append(
        str(product_records.other_month_product_id)
    )
    payload["prod_name"].append("別月改ざん商品")
    payload["prod_price"].append("400")

    _assert_product_post_rejected_without_changes(
        client,
        product_records,
        payload,
    )


def test_product_post_rejects_duplicate_product_ids_without_changes(
    client,
    product_records,
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
        client,
        product_records,
        payload,
    )


@pytest.mark.parametrize("invalid_price", ["", "abc", "-1", "1.5"])
def test_product_post_rejects_invalid_price_without_changes(
    client,
    product_records,
    invalid_price,
):
    payload = _valid_product_payload(product_records)
    payload["prod_price"][0] = invalid_price

    _assert_product_post_rejected_without_changes(
        client,
        product_records,
        payload,
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
    client,
    product_records,
    year,
    month,
):
    payload = _valid_product_payload(product_records)
    payload["year"] = year
    payload["month"] = month

    _assert_product_post_rejected_without_changes(
        client,
        product_records,
        payload,
    )
