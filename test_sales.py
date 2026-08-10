import datetime
from types import SimpleNamespace

import pytest

from models import DailySales, Product, db


def _sales_snapshot():
    return [
        (sale.id, sale.product_id, sale.date, sale.quantity)
        for sale in DailySales.query.order_by(DailySales.id).all()
    ]


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


@pytest.mark.parametrize("invalid_product_id", ["", "abc"])
def test_sales_rejects_invalid_product_ids_without_database_changes(
    client,
    sales_records,
    invalid_product_id,
):
    sales_before = _sales_snapshot()

    response = client.post(
        "/input",
        data={
            "date": sales_records.date.isoformat(),
            "product_id": [
                str(sales_records.existing_product_id),
                invalid_product_id,
            ],
            "quantity": ["9", "4"],
        },
    )

    sales_after = _sales_snapshot()

    assert response.status_code == 400
    assert len(sales_after) == len(sales_before)
    assert sales_after == sales_before
    assert DailySales.query.filter_by(
        product_id=sales_records.existing_product_id,
        date=sales_records.date,
    ).one().quantity == 5


def test_sales_rejects_duplicate_product_ids_without_database_changes(
    client,
    sales_records,
):
    sales_before = _sales_snapshot()

    response = client.post(
        "/input",
        data={
            "date": sales_records.date.isoformat(),
            "product_id": [
                str(sales_records.existing_product_id),
                str(sales_records.existing_product_id),
            ],
            "quantity": ["5", "9"],
        },
    )

    sales_after = _sales_snapshot()

    assert response.status_code == 400
    assert len(sales_after) == len(sales_before)
    assert sales_after == sales_before
    assert DailySales.query.filter_by(
        product_id=sales_records.existing_product_id,
        date=sales_records.date,
    ).one().quantity == 5


def test_sales_rejects_empty_submission_without_database_changes(
    client,
    sales_records,
):
    sales_before = _sales_snapshot()

    response = client.post(
        "/input",
        data={"date": sales_records.date.isoformat()},
    )

    sales_after = _sales_snapshot()

    assert response.status_code == 400
    assert len(sales_after) == len(sales_before)
    assert sales_after == sales_before


@pytest.mark.parametrize(
    "invalid_product_case",
    [
        "unknown_product",
        "wrong_month_product",
        "inactive_product",
    ],
)
def test_sales_rejects_unknown_wrong_month_and_inactive_products(
    client,
    sales_records,
    invalid_product_case,
):
    if invalid_product_case == "unknown_product":
        invalid_product_id = 999999
    elif invalid_product_case == "wrong_month_product":
        if sales_records.date.month == 1:
            product_year = sales_records.date.year - 1
            product_month = 12
        else:
            product_year = sales_records.date.year
            product_month = sales_records.date.month - 1

        invalid_product = Product(
            year=product_year,
            month=product_month,
            name="別月商品",
            price=400,
        )
        db.session.add(invalid_product)
        db.session.commit()
        invalid_product_id = invalid_product.id
    else:
        invalid_product = Product(
            year=sales_records.date.year,
            month=sales_records.date.month,
            name="販売終了商品",
            price=500,
            is_active=False,
        )
        db.session.add(invalid_product)
        db.session.flush()
        db.session.add(
            DailySales(
                product_id=invalid_product.id,
                date=sales_records.date,
                quantity=2,
            )
        )
        db.session.commit()
        invalid_product_id = invalid_product.id

    sales_before = [
        (sale.id, sale.product_id, sale.date, sale.quantity)
        for sale in DailySales.query.order_by(DailySales.id).all()
    ]

    response = client.post(
        "/input",
        data={
            "date": sales_records.date.isoformat(),
            "product_id": [
                str(sales_records.existing_product_id),
                str(invalid_product_id),
            ],
            "quantity": ["9", "4"],
        },
    )

    sales_after = [
        (sale.id, sale.product_id, sale.date, sale.quantity)
        for sale in DailySales.query.order_by(DailySales.id).all()
    ]

    assert response.status_code == 400
    assert len(sales_after) == len(sales_before)
    assert sales_after == sales_before
    assert DailySales.query.filter_by(
        product_id=sales_records.existing_product_id,
        date=sales_records.date,
    ).one().quantity == 5
