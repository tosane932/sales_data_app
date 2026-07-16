import os
import datetime
import logging  # 💡 1. ログモジュールをインポート
from flask import Flask, render_template, request, send_file, jsonify
from flask_migrate import Migrate
from models import db, Product, DailySales
from google import genai
from google.genai import types
import config
from prompts import build_sales_prompt

# 💡 2. ログの初期設定（デジタコのセットアップ）
# フォーマットに「日時 [レベル] メッセージ」を指定し、コンテナの標準出力に出すよう設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = config.SQLALCHEMY_DATABASE_URI
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = config.SQLALCHEMY_TRACK_MODIFICATIONS
db.init_app(app)

migrate = Migrate(app, db)



if not os.path.exists(config.PAST_FOLDER):
    os.makedirs(config.PAST_FOLDER)
    logger.info(f"Created past folder at: {config.PAST_FOLDER}")


def _generate_ai_advice(ranked_sales):
    if not ranked_sales:
        logger.warning("AI advice requested but ranked_sales is empty.")  # 💡 注意喚起
        return "売上データがまだないため、アドバイスを生成できません。"

    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            logger.error("GEMINI_API_KEY is missing from environment variables.")  # 💡 設定エラーの記録
            return "🚨【設定未完了】環境変数に GEMINI_API_KEY が登録されていません。ダッシュボードの設定を確認してください。"

        client = genai.Client(api_key=api_key)
        sales_summary = ", ".join([f"{name}: {qty}個" for name, qty in ranked_sales])
        prompt = build_sales_prompt(sales_summary)

        logger.info(f"Requesting Gemini AI advice for products: {len(ranked_sales)} items.")
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=prompt,
        )
        logger.info("Gemini AI advice generated successfully.")
        return response.text

    except Exception as e:
        # 💡 例外オブジェクト(e)をそのまま logger.error に渡すことで、詳細なスタックトレース（エラーの発生場所）も自動記録できる
        error_text = str(e)

        if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
            logger.warning(f"Gemini API rate limit hit (429): {e}")
            return (
                "☕【AIが少し休憩中です】\n"
                "短時間に多くの分析を行ったため、AIの利用制限がかかりました。"
                "10〜20秒ほど待ってから、もう一度お試しください。"
            )

        if "503" in error_text or "UNAVAILABLE" in error_text:
            logger.warning(f"Gemini API temporarily unavailable (503): {e}")
            return (
                "🥐【AIアシスタントが混み合っています】\n"
                "売上データは正常に保存・集計されています。"
                "少し時間を置いてから、もう一度「データを抽出」を押してください。"
            )

        logger.error("Unexpected error during AI advice generation", exc_info=True)
        return (
            "🚨 AIアドバイスの生成中に一時的なエラーが発生しました。"
            "時間を置いてから、もう一度お試しください。"
        )


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        year = int(request.form.get("year"))
        month = int(request.form.get("month"))
        product_names = request.form.getlist("prod_name")
        product_prices = request.form.getlist("prod_price")
        product_ids = request.form.getlist("product_id")
        products_data = []

        for product_id, name, price in zip(
            product_ids,
            product_names,
            product_prices
        ):
            if name.strip():
                products_data.append({
                    "id": int(product_id) if product_id.isdigit() else None,
                    "name": name.strip(),
                    "price": int(price) if price.isdigit() else 0
                })

        if not products_data:
            registered_months = [
                row[0]
                for row in (
                    db.session.query(Product.month)
                    .filter_by(year=year)
                    .distinct()
                    .all()
                )
            ]

            return render_template(
                "index.html",
                error="商品を1つ以上入力してください。",
                products=[],
                selected_year=year,
                selected_month=month,
                registered_months=registered_months
            )

        # 💡既存商品の価格更新と新商品の追加をログに残す
        logger.info(f"Updating product master for {year}-{month}.")

        existing_products = Product.query.filter_by(
            year=year,
            month=month
        ).all()

        existing_dict = {p.name: p for p in existing_products}

        for prod in products_data:

            if prod["name"] in existing_dict:

                # 既存商品の価格更新
                existing_dict[prod["name"]].price = prod["price"]

            else:

                # 新商品だけ追加
                db.session.add(
                    Product(
                        year=year,
                        month=month,
                        name=prod["name"],
                        price=prod["price"]
                    )
                )
        

        db.session.commit()

        logger.info(
            f"Successfully updated product master with "
            f"{len(products_data)} submitted products for {year}-{month}."
        )

        return render_template("success.html", year=year, month=month)


    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)

    today = datetime.date.today()

    if year is None:
        year = today.year

    if month is None:
        month = today.month

    products = Product.query.filter_by(
        year=year,
        month=month
    ).all()

    registered_months = (
        db.session.query(Product.month)
        .filter_by(year=year)
        .distinct()
        .all()
    )

    registered_months = [m[0] for m in registered_months]

    return render_template(
        "index.html",
        products=products,
        selected_year=year,
        selected_month=month,
        registered_months=registered_months
    )

@app.route("/dashboard")
def dashboard():
    year_param = request.args.get("year")
    month_param = request.args.get("month")
    target_year = int(year_param) if year_param else None
    target_month = int(month_param) if month_param else None

    logger.info(f"Dashboard accessed for period: year={target_year}, month={target_month}")

    sales_data = _get_sales_from_db(target_year, target_month)
    ranked_sales = sorted(sales_data.items(), key=lambda item: item[1], reverse=True)

    chart_labels = [name for name, qty in ranked_sales]
    chart_values = [qty for name, qty in ranked_sales]
    ai_advice = _generate_ai_advice(ranked_sales)

    return render_template("dashboard.html",
                           sales=sales_data,
                           ranked_sales=ranked_sales,
                           chart_labels=chart_labels,
                           chart_values=chart_values,
                           ai_advice=ai_advice,
                           year=target_year or "全期間",
                           month=target_month,
                           now=datetime.date.today())


@app.route("/api/dashboard-data")
def api_dashboard_data():
    year_param = request.args.get("year")
    month_param = request.args.get("month")
    target_year = int(year_param) if year_param else None
    target_month = int(month_param) if month_param else None

    logger.info(f"API Dashboard data requested for period: year={target_year}, month={target_month}")

    sales_data = _get_sales_from_db(target_year, target_month)
    ranked_sales = sorted(sales_data.items(), key=lambda item: item[1], reverse=True)

    chart_labels = [name for name, qty in ranked_sales]
    chart_values = [qty for name, qty in ranked_sales]
    ai_advice = _generate_ai_advice(ranked_sales)

    return jsonify({
        "ranked_sales": ranked_sales,
        "chart_labels": chart_labels,
        "chart_values": chart_values,
        "ai_advice": ai_advice,
        "period_text": f"{target_month}月度" if target_month else "全期間"
    })


@app.route("/input", methods=["GET", "POST"])
def input_sales():
    if request.method == "POST":
        date_str = request.form.get("date")
        sale_date = datetime.date.fromisoformat(date_str)
        product_ids = request.form.getlist("product_id")
        quantities = request.form.getlist("quantity")

        logger.info(f"Sales data submission received for date: {sale_date}")

        for product_id, quantity in zip(product_ids, quantities):
            if quantity.strip() == "":
                continue

            try:
                qty_int = int(float(quantity))
            except ValueError:
                logger.warning(f"Invalid quantity format skipped: product_id={product_id}, quantity={quantity}")
                continue
            if qty_int < 0:
                logger.warning(f"Negative quantity skipped: product_id={product_id}, quantity={qty_int}")
                continue

            existing = DailySales.query.filter_by(
                product_id=int(product_id),
                date=sale_date
            ).first()

            if existing:
                existing.quantity = qty_int
            else:
                sale = DailySales(
                    product_id=int(product_id),
                    date=sale_date,
                    quantity=qty_int
                )
                db.session.add(sale)

        db.session.commit()
        logger.info(f"Sales data successfully committed for date: {sale_date}")
        return render_template("input.html", success=True, products=_get_current_products(), today=datetime.date.today())

    return render_template("input.html", products=_get_current_products(), today=datetime.date.today())


@app.route("/api/greeting")
def api_greeting():
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return jsonify({"message": f"本日は{datetime.date.today().strftime('%-m月%-d日')}です。今日も一日お疲れ様でした！"})

        client = genai.Client(api_key=api_key)
        today = datetime.date.today()

        # 💡 どんなプロンプトでリクエストを投げようとしているかINFOで記録
        logger.info("Generating AI daily greeting...")
        
        prompt = f"""
            あなたはベーカリーのスタッフに話しかける、明るく親しみやすいアシスタントです。
            今日は{today.month}月{today.day}日（{['月','火','水','木','金','土','日'][today.weekday()]}曜日）です。
            以下のどれか1〜2個を自然に盛り込んで、スタッフへの一言挨拶を作ってください。
            - 今の季節感（食材・行事・気候など）
            - 今のSNSやトレンドで話題になっているパンや食品
            - 季節の新商品へのさりげない提案
            条件：
            - 全体で2〜3文
            - 親しみやすいが馴れ馴れしすぎない口調
            - 最後は「何か特別よく売れた商品はありましたか？」で締めくくる
            - 嘘の情報は入れない（SNSトレンドは「〜が話題のようですよ」程度の表現にする）
        """
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=prompt,
        )
        logger.info("AI daily greeting generated successfully.")
        return jsonify({"message": response.text})

    except Exception as e:
        logger.error(f"Failed to generate AI greeting: {e}", exc_info=True)
        today = datetime.date.today()
        return jsonify({"message": f"本日は{today.month}月{today.day}日です。今日も一日お疲れ様でした！"})


def _get_current_products():
    today = datetime.date.today()
    return Product.query.filter_by(year=today.year, month=today.month).all()


def _get_sales_from_db(target_year=None, target_month=None):
    query = db.session.query(
        Product.name,
        db.func.sum(DailySales.quantity)
    ).join(DailySales, Product.id == DailySales.product_id)

    if target_year:
        query = query.filter(db.extract("year", DailySales.date) == target_year)
    if target_month:
        query = query.filter(db.extract("month", DailySales.date) == target_month)

    results = query.group_by(Product.name).all()
    return {name: int(qty) for name, qty in results}




if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    # Renderが指定するポートを優先し、なければローカル用の5000を使う
    port = int(os.environ.get("PORT", 5000))
    
    logger.info(f"Starting Flask application with FLASK_DEBUG={debug_mode} on port {port}")
    app.run(debug=debug_mode, host="0.0.0.0", port=port)