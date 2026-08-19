import datetime

import pytest

from models import Product, DailySales
from seed_demo import seed_demo_data


def test_seed_demo_data_inserts_demo_data(flask_app, admin_dataset):
    result = seed_demo_data(
        reference_date=datetime.date(2026, 8, 13)
    )

    assert result is True
    assert Product.query.count() == 8
    assert DailySales.query.count() == 56

    products = Product.query.all()

    assert all(product.year == 2026 for product in products)
    assert all(product.month == 8 for product in products)
    assert all(product.is_active for product in products)
    assert all(
        product.dataset_id == admin_dataset.id
        for product in products
    )


def test_seed_demo_data_does_not_duplicate_existing_data(
    flask_app,
    admin_dataset,
):
    first_result = seed_demo_data(
        reference_date=datetime.date(2026, 8, 13)
    )

    second_result = seed_demo_data(
        reference_date=datetime.date(2026, 8, 13)
    )

    assert first_result is True
    assert second_result is False

    assert Product.query.count() == 8
    assert DailySales.query.count() == 56


def test_seed_demo_data_requires_admin_dataset(flask_app):
    with pytest.raises(RuntimeError, match="Admin Dataset is missing"):
        seed_demo_data(reference_date=datetime.date(2026, 8, 13))

    assert Product.query.count() == 0
    assert DailySales.query.count() == 0
