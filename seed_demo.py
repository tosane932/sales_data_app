import datetime

from sqlalchemy.exc import SQLAlchemyError

from app import app, get_admin_dataset
from models import db, Product, DailySales


DEMO_PRODUCTS = [
    ("食パン", 320),
    ("クロワッサン", 240),
    ("メロンパン", 220),
    ("あんぱん", 210),
    ("クリームパン", 220),
    ("カレーパン", 280),
    ("塩パン", 180),
    ("くるみパン", 260),
]

DEMO_QUANTITIES = [
    [24, 18, 15, 12, 10, 14, 20, 8],
    [28, 21, 17, 14, 11, 16, 23, 9],
    [22, 19, 14, 13, 9, 15, 18, 7],
    [31, 24, 20, 16, 13, 19, 26, 11],
    [35, 27, 22, 18, 15, 21, 29, 12],
    [42, 33, 26, 21, 17, 25, 34, 15],
    [38, 30, 24, 19, 16, 23, 31, 13],
]


def seed_demo_data(reference_date=None):
    reference_date = reference_date or datetime.date.today()

    with app.app_context():
        admin_dataset = get_admin_dataset()
        if admin_dataset is None:
            raise RuntimeError("Admin Dataset is missing.")

        # Admin Datasetに既存データがある場合は触らない
        admin_product_exists = Product.query.filter_by(
            dataset_id=admin_dataset.id,
        ).first() is not None
        admin_sales_exists = (
            DailySales.query
            .join(Product, DailySales.product_id == Product.id)
            .filter(Product.dataset_id == admin_dataset.id)
            .first()
            is not None
        )
        if admin_product_exists or admin_sales_exists:
            print("Demo seed skipped: database already contains data.")
            return False

        year = reference_date.year
        month = reference_date.month

        products = [
            Product(
                dataset=admin_dataset,
                year=year,
                month=month,
                name=name,
                price=price,
                is_active=True,
            )
            for name, price in DEMO_PRODUCTS
        ]

        try:
            db.session.add_all(products)

            # Product.id を確定させる
            db.session.flush()

            for day_offset, quantities in enumerate(DEMO_QUANTITIES, start=1):
                sale_date = datetime.date(year, month, day_offset)

                for product, quantity in zip(products, quantities):
                    db.session.add(
                        DailySales(
                            product_id=product.id,
                            date=sale_date,
                            quantity=quantity,
                        )
                    )

            db.session.commit()

        except SQLAlchemyError:
            db.session.rollback()
            raise

        print(
            f"Demo seed completed: "
            f"{len(products)} products, "
            f"{len(DEMO_QUANTITIES)} days of sales."
        )

        return True


if __name__ == "__main__":
    seed_demo_data()
