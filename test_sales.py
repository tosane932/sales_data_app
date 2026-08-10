import datetime
from types import SimpleNamespace

import pytest

from models import DailySales, Product, db


@pytest.fixture()
def sales_records(flask_app):
    sale_date = datetime.date.today()
    existing_product = Product(
        year=sale_date.year,
        month=sale_date.month,
        name="既存商品",
        price=200,
    )
    new_product = Product(
        year=sale_date.year,
        month=sale_date.month,
        name="新規売上商品",
        price=300,
    )
    db.session.add_all([existing_product, new_product])
    db.session.flush()

    existing_sale = DailySales(
        product_id=existing_product.id,
        date=sale_date,
        quantity=5,
    )
    db.session.add(existing_sale)
    db.session.commit()

    return SimpleNamespace(
        date=sale_date,
        existing_product_id=existing_product.id,
        new_product_id=new_product.id,
    )


def test_valid_sales_post_updates_existing_and_adds_new_sale(
    client,
    sales_records,
):
    response = client.post(
        "/input",
        data={
            "date": sales_records.date.isoformat(),
            "product_id": [
                str(sales_records.existing_product_id),
                str(sales_records.new_product_id),
            ],
            "quantity": ["9", "0"],
        },
    )

    assert response.status_code == 200
    assert DailySales.query.count() == 2
    assert DailySales.query.filter_by(
        product_id=sales_records.existing_product_id,
        date=sales_records.date,
    ).one().quantity == 9
    assert DailySales.query.filter_by(
        product_id=sales_records.new_product_id,
        date=sales_records.date,
    ).one().quantity == 0


@pytest.mark.parametrize(
    "invalid_case",
    [
        "invalid_date",
        "empty_quantity",
        "string_quantity",
        "negative_quantity",
        "decimal_quantity",
        "missing_quantity",
        "extra_quantity",
    ],
)
def test_invalid_sales_post_rejects_entire_request(
    client,
    sales_records,
    invalid_case,
):
    date_value = sales_records.date.isoformat()
    product_ids = [
        str(sales_records.existing_product_id),
        str(sales_records.new_product_id),
    ]
    quantities = ["9", "4"]

    if invalid_case == "invalid_date":
        date_value = "not-a-date"
    elif invalid_case == "empty_quantity":
        quantities[1] = ""
    elif invalid_case == "string_quantity":
        quantities[1] = "four"
    elif invalid_case == "negative_quantity":
        quantities[1] = "-1"
    elif invalid_case == "decimal_quantity":
        quantities[1] = "1.5"
    elif invalid_case == "missing_quantity":
        quantities.pop()
    elif invalid_case == "extra_quantity":
        quantities.append("7")

    response = client.post(
        "/input",
        data={
            "date": date_value,
            "product_id": product_ids,
            "quantity": quantities,
        },
    )

    assert response.status_code == 400
    assert DailySales.query.count() == 1
    assert DailySales.query.filter_by(
        product_id=sales_records.existing_product_id,
        date=sales_records.date,
    ).one().quantity == 5
    assert DailySales.query.filter_by(
        product_id=sales_records.new_product_id,
        date=sales_records.date,
    ).first() is None
