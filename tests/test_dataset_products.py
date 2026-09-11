import datetime
import uuid

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

from models import Dataset, Product, db


BASE_TIME = datetime.datetime(
    2026,
    8,
    19,
    12,
    0,
    tzinfo=datetime.timezone.utc,
)


@pytest.fixture(autouse=True)
def enable_sqlite_foreign_keys(flask_app):
    db.session.execute(text("PRAGMA foreign_keys = ON"))

    enabled = db.session.execute(text("PRAGMA foreign_keys")).scalar_one()
    assert enabled == 1


def _guest_dataset():
    return Dataset(
        kind="guest",
        system_key=None,
        created_at=BASE_TIME,
        last_activity_at=BASE_TIME,
        absolute_expires_at=BASE_TIME + datetime.timedelta(hours=2),
    )


def _product(name, *, dataset=None, dataset_id=None):
    if (dataset is None) == (dataset_id is None):
        raise ValueError(
            "Specify exactly one of dataset or dataset_id for a Product."
        )

    product_data = {
        "year": 2026,
        "month": 8,
        "name": name,
        "price": 200,
    }

    if dataset is not None:
        product_data["dataset"] = dataset
    if dataset_id is not None:
        product_data["dataset_id"] = dataset_id

    return Product(**product_data)


def test_product_can_belong_to_dataset():
    dataset = _guest_dataset()
    product = _product("商品A", dataset=dataset)
    db.session.add_all([dataset, product])
    db.session.commit()

    assert product.dataset_id == dataset.id
    assert product.dataset == dataset
    assert product in dataset.products


def test_dataset_can_have_multiple_products():
    dataset = _guest_dataset()
    product_a = _product("商品A", dataset=dataset)
    product_b = _product("商品B", dataset=dataset)
    db.session.add_all([dataset, product_a, product_b])
    db.session.commit()

    saved_products = Product.query.filter_by(
        dataset_id=dataset.id,
    ).order_by(Product.name).all()

    assert saved_products == [product_a, product_b]
    assert dataset.products == [product_a, product_b]


def test_different_datasets_keep_products_separate():
    dataset_a = _guest_dataset()
    dataset_b = _guest_dataset()
    product_a = _product("Dataset Aの商品", dataset=dataset_a)
    product_b = _product("Dataset Bの商品", dataset=dataset_b)
    db.session.add_all([dataset_a, dataset_b, product_a, product_b])
    db.session.commit()

    assert Product.query.filter_by(dataset_id=dataset_a.id).all() == [
        product_a
    ]
    assert Product.query.filter_by(dataset_id=dataset_b.id).all() == [
        product_b
    ]


def test_same_product_name_can_exist_in_different_datasets():
    dataset_a = _guest_dataset()
    dataset_b = _guest_dataset()
    product_a = _product("同名商品", dataset=dataset_a)
    product_b = _product("同名商品", dataset=dataset_b)
    db.session.add_all([dataset_a, dataset_b, product_a, product_b])
    db.session.commit()

    same_name_products = Product.query.filter_by(name="同名商品").all()

    assert len(same_name_products) == 2
    assert {product.dataset_id for product in same_name_products} == {
        dataset_a.id,
        dataset_b.id,
    }


def test_product_with_unknown_dataset_id_is_rejected():
    product = _product("孤立商品", dataset_id=uuid.uuid4())
    db.session.add(product)

    with pytest.raises(IntegrityError) as error:
        db.session.commit()

    db.session.rollback()
    assert "FOREIGN KEY constraint failed" in str(error.value)


def test_deleting_dataset_cascades_to_its_products_in_sqlite():
    dataset = _guest_dataset()
    product_a = _product("商品A", dataset=dataset)
    product_b = _product("商品B", dataset=dataset)
    db.session.add_all([dataset, product_a, product_b])
    db.session.commit()

    dataset_id = dataset.id
    product_ids = [product_a.id, product_b.id]

    db.session.execute(delete(Dataset).where(Dataset.id == dataset_id))
    db.session.commit()
    db.session.expire_all()

    assert db.session.get(Dataset, dataset_id) is None
    assert all(
        db.session.get(Product, product_id) is None
        for product_id in product_ids
    )
