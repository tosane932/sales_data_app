import os
import datetime
import hashlib
import hmac
import logging  # 💡 1. ログモジュールをインポート
import uuid
from functools import wraps
from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
)
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import InternalServerError
from werkzeug.security import check_password_hash
from models import db, Dataset, Product, DailySales
from google import genai
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
app.config["SECRET_KEY"] = config.SECRET_KEY
app.config["ADMIN_USERNAME"] = config.ADMIN_USERNAME
app.config["ADMIN_PASSWORD_HASH"] = config.ADMIN_PASSWORD_HASH
csrf = CSRFProtect(app)
db.init_app(app)

migrate = Migrate(app, db)

login_manager = LoginManager(app)
login_manager.login_view = "login"


class AdminUser(UserMixin):
    id = "admin"
    is_admin = True
    is_guest = False


class GuestUser(UserMixin):
    is_admin = False
    is_guest = True

    def __init__(self, dataset_id):
        self.dataset_id = dataset_id

    @property
    def id(self):
        return f"guest:{self.dataset_id}"


ADMIN_AUTH_FINGERPRINT_SESSION_KEY = "admin_auth_fingerprint"
GUEST_ABSOLUTE_LIFETIME = datetime.timedelta(hours=2)
GUEST_IDLE_TIMEOUT = datetime.timedelta(minutes=30)


def _as_utc(value):
    """DBから取得した日時をUTCのaware datetimeへそろえる。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc)


def _get_admin_auth_fingerprint(password_hash):
    if not isinstance(password_hash, str) or not password_hash:
        return None

    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()


@login_manager.user_loader
def load_user(user_id):
    if user_id == AdminUser.id:
        if not app.config.get("ADMIN_USERNAME"):
            return None

        current_fingerprint = _get_admin_auth_fingerprint(
            app.config.get("ADMIN_PASSWORD_HASH")
        )
        session_fingerprint = session.get(
            ADMIN_AUTH_FINGERPRINT_SESSION_KEY
        )
        if not current_fingerprint or not isinstance(session_fingerprint, str):
            return None
        if not hmac.compare_digest(current_fingerprint, session_fingerprint):
            return None

        return AdminUser()

    if not isinstance(user_id, str) or not user_id.startswith("guest:"):
        return None

    guest_dataset_id_text = user_id.removeprefix("guest:")
    try:
        guest_dataset_id = uuid.UUID(guest_dataset_id_text)
    except (ValueError, AttributeError):
        return None

    try:
        guest_dataset = Dataset.query.filter_by(
            id=guest_dataset_id,
            kind="guest",
            system_key=None,
        ).one_or_none()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Failed to restore a Guest from its Dataset.")
        return None

    if guest_dataset is None:
        return None

    absolute_expires_at = _as_utc(
        guest_dataset.absolute_expires_at
    )
    last_activity_at = _as_utc(
        guest_dataset.last_activity_at
    )
    now = datetime.datetime.now(datetime.timezone.utc)

    if absolute_expires_at is None or absolute_expires_at <= now:
        return None

    if (
        last_activity_at is None
        or last_activity_at + GUEST_IDLE_TIMEOUT <= now
    ):
        return None

    return GuestUser(guest_dataset.id)


def admin_required(view_function):
    """Adminだけが業務routeへアクセスできるようにする。"""
    @wraps(view_function)
    @login_required
    def wrapped_view(*args, **kwargs):
        if not getattr(current_user, "is_admin", False):
            abort(403)
        return view_function(*args, **kwargs)

    return wrapped_view


def admin_or_guest_required(view_function):
    """Adminまたは正規Guestだけが業務routeへアクセスできるようにする。"""
    @wraps(view_function)
    @login_required
    def wrapped_view(*args, **kwargs):
        principal = current_user._get_current_object()

        if not isinstance(principal, (AdminUser, GuestUser)):
            abort(403)

        return view_function(*args, **kwargs)

    return wrapped_view


def require_current_dataset():
    """現在の認証利用者が使用できるDatasetだけを返す。"""
    if not current_user.is_authenticated:
        abort(403)

    principal = current_user._get_current_object()
    if isinstance(principal, AdminUser):
        dataset_filters = {
            "kind": "admin",
            "system_key": "admin",
        }
        missing_status_code = 500
    elif isinstance(principal, GuestUser):
        dataset_filters = {
            "id": principal.dataset_id,
            "kind": "guest",
            "system_key": None,
        }
        missing_status_code = 403
    else:
        abort(403)

    try:
        dataset = Dataset.query.filter_by(**dataset_filters).one_or_none()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Failed to resolve the current Dataset.")
        abort(503)

    if dataset is None:
        if missing_status_code == 500:
            logger.error("The Admin Dataset is missing.")
        abort(missing_status_code)

    return dataset


def start_guest_session():
    """Guest Datasetを作成し、対応するGuest identityを発行する。"""
    if current_user.is_authenticated:
        abort(409)

    now = datetime.datetime.now(datetime.timezone.utc)
    guest_dataset = Dataset(
        kind="guest",
        system_key=None,
        created_at=now,
        last_activity_at=now,
        absolute_expires_at=now + GUEST_ABSOLUTE_LIFETIME,
    )

    try:
        db.session.add(guest_dataset)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Failed to create a Guest Dataset.")
        abort(503)

    guest_user = GuestUser(guest_dataset.id)
    if not login_user(guest_user):
        logger.error("Failed to establish the Guest identity.")
        abort(500)

    return guest_dataset


def get_admin_dataset():
    """system_keyから管理者Datasetを取得し、異常時は安全に失敗する。"""
    try:
        return Dataset.query.filter_by(
            kind="admin",
            system_key="admin",
        ).one_or_none()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Failed to load the admin Dataset.")
        return None


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
        error_text = str(e)

        if "GenerateRequestsPerDayPerProjectPerModel-FreeTier" in error_text:
            logger.warning(
                f"Gemini API daily free-tier quota reached: {e}"
            )
            return (
                "☕【本日のAI分析回数が上限に達しました】\n"
                "売上データの保存と集計は正常です。"
                "時間を置いてから、あらためてAI分析をお試しください。"
            )

        if "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
            logger.warning(
                f"Gemini API rate limit hit (429): {e}"
            )
            return (
                "☕【AIが少し休憩中です】\n"
                "短時間に多くの分析を行ったため、"
                "AIの利用制限がかかりました。"
                "少し時間を置いてから、もう一度お試しください。"
            )

        if "503" in error_text or "UNAVAILABLE" in error_text:
            logger.warning(
                f"Gemini API temporarily unavailable (503): {e}"
            )
            return (
                "🥐【AIアシスタントが混み合っています】\n"
                "売上データは正常に保存・集計されています。"
                "少し時間を置いてから、もう一度お試しください。"
            )

        logger.error(
            "Unexpected error during AI advice generation",
            exc_info=True
        )
        return (
            "🚨 AIアドバイスの生成中に一時的なエラーが発生しました。"
            "時間を置いてから、もう一度お試しください。"
        )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        configured_username = app.config.get("ADMIN_USERNAME")
        configured_password_hash = app.config.get("ADMIN_PASSWORD_HASH")

        password_matches = False
        if configured_password_hash:
            try:
                password_matches = check_password_hash(
                    configured_password_hash,
                    password,
                )
            except (TypeError, ValueError):
                logger.exception("Invalid administrator password hash configuration.")

        if (
            configured_username
            and username == configured_username
            and password_matches
        ):
            login_user(AdminUser())
            session[ADMIN_AUTH_FINGERPRINT_SESSION_KEY] = (
                _get_admin_auth_fingerprint(configured_password_hash)
            )
            return redirect(url_for("index"))

        logger.warning("Administrator login failed.")
        return render_template(
            "login.html",
            error="ユーザー名またはパスワードが正しくありません。",
        ), 401

    return render_template("login.html")


@app.route("/", methods=["GET", "POST"])
@admin_or_guest_required
def index():
    try:
        current_dataset = require_current_dataset()
    except InternalServerError:
        if request.method == "POST":
            return "管理者データ領域が見つかりません。", 500
        current_dataset = None

    if request.method == "POST":
        if not current_user.is_authenticated:
            return login_manager.unauthorized()

        try:
            year = int(request.form.get("year"))
            month = int(request.form.get("month"))
        except (TypeError, ValueError):
            logger.warning("Rejected product update with invalid year or month.")
            return "年月が正しくありません。", 400

        if month < 1 or month > 12:
            logger.warning("Rejected product update with out-of-range month.")
            return "月は1から12で指定してください。", 400

        product_names = request.form.getlist("prod_name")
        product_prices = request.form.getlist("prod_price")
        product_ids = request.form.getlist("product_id")

        if not (
            len(product_ids) == len(product_names) == len(product_prices)
        ):
            logger.warning("Rejected product update with mismatched field lengths.")
            return "商品データの件数が一致しません。", 400

        products_data = []
        seen_product_ids = set()

        for product_id, name, price in zip(
            product_ids,
            product_names,
            product_prices
        ):
            existing_product = None

            if product_id:
                try:
                    parsed_product_id = int(product_id)
                except ValueError:
                    logger.warning("Rejected product update with invalid product ID.")
                    return "商品IDが正しくありません。", 400

                if parsed_product_id in seen_product_ids:
                    logger.warning("Rejected product update with duplicate product ID.")
                    return "同じ商品が複数回送信されています。", 400

                existing_product = Product.query.filter_by(
                    id=parsed_product_id,
                    dataset_id=current_dataset.id,
                ).one_or_none()
                if existing_product is None:
                    logger.warning("Rejected product update with unknown product ID.")
                    return "指定された商品が見つかりません。", 400

                if (
                    existing_product.year != year
                    or existing_product.month != month
                ):
                    logger.warning("Rejected product update for another year or month.")
                    return "指定された商品は選択年月の商品ではありません。", 400

                seen_product_ids.add(parsed_product_id)
            else:
                parsed_product_id = None

            if not price.isascii() or not price.isdigit():
                logger.warning("Rejected product update with invalid price.")
                return "価格は0以上の整数で入力してください。", 400

            if name.strip():
                products_data.append({
                    "id": parsed_product_id,
                    "product": existing_product,
                    "name": name.strip(),
                    "price": int(price)
                })

        if not products_data:
            registered_months = [
                row[0]
                for row in (
                    db.session.query(Product.month)
                    .filter_by(
                        dataset_id=current_dataset.id,
                        year=year,
                    )
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
            dataset_id=current_dataset.id,
            year=year,
            month=month
        ).all()

        submitted_ids = {
            prod["id"]
            for prod in products_data
            if prod["id"] is not None
        }

        try:
            for prod in products_data:
                product_id = prod["id"]

                if product_id is not None:
                    # 既存商品の商品名と価格を更新
                    existing_product = prod["product"]
                    existing_product.name = prod["name"]
                    existing_product.price = prod["price"]
                    existing_product.is_active = True

                else:
                    # IDがない商品は新規追加
                    db.session.add(
                        Product(
                            dataset=current_dataset,
                            year=year,
                            month=month,
                            name=prod["name"],
                            price=prod["price"]
                        )
                    )

            for product in existing_products:
                if product.id not in submitted_ids:
                    product.is_active = False

            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to save product master.")
            return "商品マスタを保存できませんでした。", 500

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

    if current_dataset is None:
        products = []
        registered_months = []
    else:
        products = Product.query.filter_by(
            dataset_id=current_dataset.id,
            year=year,
            month=month,
            is_active=True
        ).all()

        registered_months = (
            db.session.query(Product.month)
            .filter_by(
                dataset_id=current_dataset.id,
                year=year,
            )
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

def _get_optional_integer_query_parameter(name):
    value = request.args.get(name)
    if not value:
        return None

    try:
        return int(value)
    except ValueError:
        abort(400)


@app.route("/dashboard")
@admin_or_guest_required
def dashboard():
    target_year = _get_optional_integer_query_parameter("year")
    target_month = _get_optional_integer_query_parameter("month")
    current_dataset = require_current_dataset()

    logger.info(f"Dashboard accessed for period: year={target_year}, month={target_month}")

    sales_data = _get_sales_from_db(
        current_dataset,
        target_year,
        target_month,
    )
    ranked_sales = sorted(sales_data.items(), key=lambda item: item[1], reverse=True)

    chart_labels = [name for name, qty in ranked_sales]
    chart_values = [qty for name, qty in ranked_sales]
    ai_advice = (
        "売上ランキングとグラフを確認できます。"
        "さらに詳しい改善案を知りたい場合は、"
        "「詳しいアドバイスを聞く」ボタンを押してください。"
    )

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
@admin_or_guest_required
def api_dashboard_data():
    target_year = _get_optional_integer_query_parameter("year")
    target_month = _get_optional_integer_query_parameter("month")
    current_dataset = require_current_dataset()

    logger.info(f"API Dashboard data requested for period: year={target_year}, month={target_month}")

    sales_data = _get_sales_from_db(
        current_dataset,
        target_year,
        target_month,
    )
    ranked_sales = sorted(sales_data.items(), key=lambda item: item[1], reverse=True)

    chart_labels = [name for name, qty in ranked_sales]
    chart_values = [qty for name, qty in ranked_sales]

    return jsonify({
        "ranked_sales": ranked_sales,
        "chart_labels": chart_labels,
        "chart_values": chart_values,
        "ai_advice": (
            "売上ランキングとグラフを更新しました。"
            "詳しい改善案を確認する場合は、"
            "「詳しいアドバイスを聞く」ボタンを押してください。"
        ),
        "period_text": f"{target_month}月度" if target_month else "全期間"
    })

@app.route("/api/ai-advice")
@admin_or_guest_required
def api_ai_advice():
    target_year = _get_optional_integer_query_parameter("year")
    target_month = _get_optional_integer_query_parameter("month")
    current_dataset = require_current_dataset()

    logger.info(
        f"AI advice requested for period: "
        f"year={target_year}, month={target_month}"
    )

    sales_data = _get_sales_from_db(
        current_dataset,
        target_year,
        target_month
    )

    ranked_sales = sorted(
        sales_data.items(),
        key=lambda item: item[1],
        reverse=True
    )

    ai_advice = _generate_ai_advice(ranked_sales)

    return jsonify({
        "ai_advice": ai_advice
    })

@app.route("/input", methods=["GET", "POST"])
@admin_or_guest_required
def input_sales():
    today = datetime.date.today()
    current_dataset = require_current_dataset()

    if request.method == "POST":
        if not current_user.is_authenticated:
            return login_manager.unauthorized()

        date_str = request.form.get("date")
        try:
            sale_date = datetime.date.fromisoformat(date_str)
        except (TypeError, ValueError):
            logger.warning(f"Invalid sales date rejected: {date_str}")
            return "売上日の日付形式が正しくありません。", 400

        product_ids = request.form.getlist("product_id")
        quantities = request.form.getlist("quantity")

        if not product_ids or not quantities:
            logger.warning("Empty sales submission rejected.")
            return "商品と販売数量を入力してください。", 400

        if len(product_ids) != len(quantities):
            logger.warning(
                "Mismatched sales input lengths rejected: "
                f"product_ids={len(product_ids)}, "
                f"quantities={len(quantities)}"
            )
            return "商品と販売数量の件数が一致しません。", 400

        validated_sales = []
        seen_product_ids = set()
        for product_id, quantity in zip(product_ids, quantities):
            try:
                product_id_int = int(product_id)
            except (TypeError, ValueError):
                logger.warning(
                    f"Invalid product ID rejected: product_id={product_id}"
                )
                return "商品IDが正しくありません。", 400

            if product_id_int in seen_product_ids:
                logger.warning(
                    f"Duplicate product ID rejected: product_id={product_id}"
                )
                return "同じ商品が複数回送信されています。", 400

            seen_product_ids.add(product_id_int)

            if not quantity.isascii() or not quantity.isdigit():
                logger.warning(
                    "Invalid quantity rejected: "
                    f"product_id={product_id}, quantity={quantity}"
                )
                return "販売数量は0以上の整数で入力してください。", 400

            validated_sales.append((product_id_int, int(quantity)))

        validated_product_sales = []
        for product_id, qty_int in validated_sales:
            product = Product.query.filter_by(
                id=product_id,
                dataset_id=current_dataset.id,
            ).one_or_none()

            if product is None:
                logger.warning(
                    f"Unknown product rejected: product_id={product_id}"
                )
                return "指定された商品が存在しません。", 400

            if (
                product.year != sale_date.year
                or product.month != sale_date.month
            ):
                logger.warning(
                    "Product outside sales month rejected: "
                    f"product_id={product_id}, date={sale_date}"
                )
                return "売上日と商品の対象年月が一致しません。", 400

            if not product.is_active:
                logger.warning(
                    f"Inactive product rejected: product_id={product_id}"
                )
                return "販売終了商品には売上を登録できません。", 400

            validated_product_sales.append((product, qty_int))

        logger.info(
            f"Sales data submission received for date: {sale_date}"
        )

        try:
            for product, qty_int in validated_product_sales:
                existing = DailySales.query.filter_by(
                    product_id=product.id,
                    date=sale_date
                ).first()

                if existing:
                    existing.quantity = qty_int
                else:
                    sale = DailySales(
                        product_id=product.id,
                        date=sale_date,
                        quantity=qty_int
                    )
                    db.session.add(sale)

            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to save sales data.")
            return "売上データを保存できませんでした。", 500

        logger.info(
            f"Sales data successfully committed for date: {sale_date}"
        )

        return render_template(
            "input.html",
            success=True,
            products=_get_current_products(current_dataset),
            today=today,
            today_sales=_get_today_sales_map(today, current_dataset)
        )

    return render_template(
        "input.html",
        products=_get_current_products(current_dataset),
        today=today,
        today_sales=_get_today_sales_map(today, current_dataset)
    )

@app.route("/api/greeting")
@admin_or_guest_required
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


def _get_current_products(current_dataset):
    today = datetime.date.today()

    return Product.query.filter_by(
        dataset_id=current_dataset.id,
        year=today.year,
        month=today.month,
        is_active=True
    ).all()

def _get_today_sales_map(target_date, current_dataset):
    """指定日の商品別売上個数を辞書で返す。"""
    sales = (
        DailySales.query
        .join(Product, DailySales.product_id == Product.id)
        .filter(
            DailySales.date == target_date,
            Product.dataset_id == current_dataset.id,
        )
        .all()
    )

    return {
        sale.product_id: sale.quantity
        for sale in sales
    }

def _get_sales_from_db(
    current_dataset,
    target_year=None,
    target_month=None,
):
    query = db.session.query(
        Product.name,
        db.func.sum(DailySales.quantity)
    ).join(
        DailySales,
        Product.id == DailySales.product_id,
    ).filter(
        Product.dataset_id == current_dataset.id,
    )

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
