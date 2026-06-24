import os
import datetime
from flask import Flask, render_template, request, send_file, jsonify
from models import db, Product, DailySales
# 最新の Google GenAI SDK クライアントモジュールを導入
from google import genai
from google.genai import types

# 一元管理された設定情報をインポート
import config

from prompts import build_sales_prompt

app = Flask(__name__)


app.config["SQLALCHEMY_DATABASE_URI"] = config.SQLALCHEMY_DATABASE_URI
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = config.SQLALCHEMY_TRACK_MODIFICATIONS
db.init_app(app)

with app.app_context():
    db.create_all()


# アプリ起動時に保存先フォルダが存在しない場合は自動生成する
if not os.path.exists(config.PAST_FOLDER):
    os.makedirs(config.PAST_FOLDER)


def _generate_ai_advice(ranked_sales):
    """
    売上データを解析し、Gemini APIを用いて経営アドバイスのテキストを生成する共通関数。
    通常表示と非同期通信（API）の両方から呼び出され、エラーハンドリングを一元化している。
    """
    if not ranked_sales:
        return "売上データがまだないため、アドバイスを生成できません。"

    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        # デプロイ環境を考慮し、特定のOS名に依存しない汎用的なエラーメッセージに修正
        if not api_key:
            return "🚨【設定未完了】環境変数に GEMINI_API_KEY が登録されていません。ダッシュボードの設定を確認してください。"

        # GenAIクライアントの初期化およびコンサルタントとしてのプロンプト構築
        client = genai.Client(api_key=api_key)
        sales_summary = ", ".join([f"{name}: {qty}個" for name, qty in ranked_sales])

        prompt = build_sales_prompt(sales_summary)

        # configで一元管理しているモデル名を使用してアドバイスをリクエスト
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=prompt,
        )
        return response.text

    except Exception as e:
        # 短時間での過剰なリクエストによりAPIの速度制限（429エラー）に到達した場合の処理
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            return "☕【AIが少し休憩中です】\n短時間にたくさんデータを抽出したため、AIの速度制限がかかっております。お手数ですが、10秒〜20秒ほどあけて、もう一度「データを抽出」ボタンを押してみてください。"
        return f"AIアドバイスを生成中に一時的なエラーが発生しました。（デバッグ用: {e}）"


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        year = int(request.form.get("year"))
        month = int(request.form.get("month"))

        product_names = request.form.getlist("prod_name")
        product_prices = request.form.getlist("prod_price")

        products_data = []
        for name, price in zip(product_names, product_prices):
            if name.strip():
                products_data.append({
                    "name": name.strip(),
                    "price": int(price) if price.isdigit() else 0
                })

        if not products_data:
            return render_template("index.html", error="商品を1つ以上入力してください。")

        # 同じ年月の商品が既に登録されている場合は上書きを防ぐため一度削除してから再登録
        existing = Product.query.filter_by(year=year, month=month).all()
        for p in existing:
            db.session.delete(p)
        db.session.commit()

        # フォームで受け取った商品情報をProductテーブルに登録
        for prod in products_data:
            product = Product(year=year, month=month, name=prod["name"], price=prod["price"])
            db.session.add(product)
        db.session.commit()

        return render_template("success.html", year=year, month=month)

    return render_template("index.html")


@app.route("/dashboard")
def dashboard():
    """
    通常の画面遷移でダッシュボードを表示する際のルーティング。
    初期表示時、またはクエリパラメータによる期間絞り込み時に動作する。
    """
    year_param = request.args.get("year")
    month_param = request.args.get("month")

    target_year = int(year_param) if year_param else None
    target_month = int(month_param) if month_param else None

    # ExcelファイルではなくDBのDailySalesテーブルから売上を集計する
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
    """
    画面をリロードせずにデータを非同期（Ajax）で更新するためのエンドポイント。
    """
    year_param = request.args.get("year")
    month_param = request.args.get("month")

    target_year = int(year_param) if year_param else None
    target_month = int(month_param) if month_param else None

    # ExcelファイルではなくDBから集計して返す
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
    """
    日次の売上数量をWebフォームで入力し、DailySalesテーブルに保存するルート。
    """
    if request.method == "POST":
        date_str = request.form.get("date")
        sale_date = datetime.date.fromisoformat(date_str)

        product_ids = request.form.getlist("product_id")
        quantities = request.form.getlist("quantity")

        for product_id, quantity in zip(product_ids, quantities):
            if quantity.strip() == "":
                continue

            # 同じ日付・商品の入力が既にある場合は上書きして二重登録を防ぐ
            existing = DailySales.query.filter_by(
                product_id=int(product_id),
                date=sale_date
            ).first()

            if existing:
                existing.quantity = int(quantity)
            else:
                sale = DailySales(
                    product_id=int(product_id),
                    date=sale_date,
                    quantity=int(quantity)
                )
                db.session.add(sale)

        db.session.commit()
        return render_template("input.html", success=True, products=_get_current_products(), today=datetime.date.today())

    return render_template("input.html", products=_get_current_products(), today=datetime.date.today())


@app.route("/api/greeting")
def api_greeting():
    """
    日次売上入力画面の上部に表示する、今日の日付に応じたAIの一言を生成するエンドポイント。
    """
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return jsonify({"message": f"本日は{datetime.date.today().strftime('%-m月%-d日')}です。今日も一日お疲れ様でした！"})

        client = genai.Client(api_key=api_key)
        today = datetime.date.today()

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
        return jsonify({"message": response.text})

    except Exception as e:
        today = datetime.date.today()
        return jsonify({"message": f"本日は{today.month}月{today.day}日です。今日も一日お疲れ様でした！"})


def _get_current_products():
    """今月登録されている商品一覧をDBから取得する。"""
    today = datetime.date.today()
    return Product.query.filter_by(year=today.year, month=today.month).all()


def _get_sales_from_db(target_year=None, target_month=None):
    """
    DailySalesテーブルとProductテーブルをJOINし、
    指定期間の商品別売上数量を集計して辞書で返す。
    """
    query = db.session.query(
        Product.name,
        db.func.sum(DailySales.quantity)
    ).join(DailySales, Product.id == DailySales.product_id)

    # 年・月の絞り込み条件をクエリに動的に追加する
    if target_year:
        query = query.filter(
            db.extract("year", DailySales.date) == target_year
        )
    if target_month:
        query = query.filter(
            db.extract("month", DailySales.date) == target_month
        )

    results = query.group_by(Product.name).all()
    return {name: int(qty) for name, qty in results}


if __name__ == "__main__":
    # debug=Trueは環境変数で制御し、本番環境での意図しない有効化を防ぐ
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug_mode, host="0.0.0.0", port=5000)