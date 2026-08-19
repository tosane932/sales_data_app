import datetime
import uuid

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def utc_now():
    """UTCのtimezone情報を持つ現在日時を返す。"""
    return datetime.datetime.now(datetime.timezone.utc)


class Dataset(db.Model):
    """管理者またはGuestごとに分離されたデータ領域を表すテーブル。"""
    __tablename__ = "datasets"
    __table_args__ = (
        db.CheckConstraint(
            "kind IN ('admin', 'guest')",
            name="ck_datasets_kind",
        ),
        db.CheckConstraint(
            "(kind = 'admin' AND system_key = 'admin') OR "
            "(kind = 'guest' AND system_key IS NULL)",
            name="ck_datasets_system_key_by_kind",
        ),
        db.CheckConstraint(
            "(kind = 'admin' AND absolute_expires_at IS NULL) OR "
            "(kind = 'guest' AND absolute_expires_at IS NOT NULL)",
            name="ck_datasets_absolute_expiry_by_kind",
        ),
        db.CheckConstraint(
            "last_activity_at >= created_at",
            name="ck_datasets_activity_not_before_creation",
        ),
        db.CheckConstraint(
            "absolute_expires_at IS NULL OR absolute_expires_at > created_at",
            name="ck_datasets_expiry_after_creation",
        ),
    )

    id = db.Column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    kind = db.Column(db.String(16), nullable=False)
    system_key = db.Column(db.String(100), nullable=True, unique=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=db.func.now(),
    )
    last_activity_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=db.func.now(),
    )
    absolute_expires_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )
    products = db.relationship(
        "Product",
        back_populates="dataset",
        cascade="all, delete",
        passive_deletes=True,
    )


class Product(db.Model):
    """その月に登録された商品マスタ（商品名・単価）を表すテーブル。"""
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    dataset_id = db.Column(
        db.Uuid(as_uuid=True),
        db.ForeignKey(
            "datasets.id",
            name="fk_products_dataset_id_datasets",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Integer, nullable=False)
    dataset = db.relationship("Dataset", back_populates="products")
    
    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.true()
    )
    # 1つの商品は複数の日別実績(daily_sales)を持つ
    daily_sales = db.relationship("DailySales", backref="product", lazy=True)


class DailySales(db.Model):
    """商品ごとの日別の販売数量を記録するテーブル。"""
    __tablename__ = "daily_sales"
    __table_args__ = (
        db.UniqueConstraint(
            "product_id",
            "date",
            name="uq_daily_sales_product_date"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=0)
