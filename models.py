from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Product(db.Model):
    """その月に登録された商品マスタ（商品名・単価）を表すテーブル。"""
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Integer, nullable=False)
    
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

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=0)