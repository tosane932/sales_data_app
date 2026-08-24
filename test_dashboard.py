import datetime
from unittest.mock import Mock

import pytest

import app as app_module
from models import DailySales, Dataset, Product, db


FIXED_AI_ADVICE = (
    "売上ランキングとグラフを更新しました。"
    "詳しい改善案を確認する場合は、"
    "「詳しいアドバイスを聞く」ボタンを押してください。"
)


def _product_snapshot():
    return [
        (
            product.id,
            product.year,
            product.month,
            product.name,
            product.price,
            product.is_active,
        )
        for product in Product.query.order_by(Product.id).all()
    ]


def _sales_snapshot():
    return [
        (sale.id, sale.product_id, sale.date, sale.quantity)
        for sale in DailySales.query.order_by(DailySales.id).all()
    ]


@pytest.fixture()
def dashboard_records(flask_app, admin_dataset, monkeypatch):
    def reject_gemini_client(*args, **kwargs):
        raise AssertionError("Gemini Client must not be used by dashboard API")

    monkeypatch.setattr(app_module.genai, "Client", reject_gemini_client)

    product_a = Product(
        dataset=admin_dataset,
        year=2026,
        month=8,
        name="商品A",
        price=100,
    )
    product_b = Product(
        dataset=admin_dataset,
        year=2026,
        month=8,
        name="商品B",
        price=200,
    )
    july_product = Product(
        dataset=admin_dataset,
        year=2026,
        month=7,
        name="7月商品",
        price=300,
    )
    previous_year_product = Product(
        dataset=admin_dataset,
        year=2025,
        month=8,
        name="前年商品",
        price=400,
    )
    db.session.add_all([
        product_a,
        product_b,
        july_product,
        previous_year_product,
    ])
    db.session.flush()

    db.session.add_all([
        DailySales(
            product_id=product_a.id,
            date=datetime.date(2026, 8, 1),
            quantity=3,
        ),
        DailySales(
            product_id=product_a.id,
            date=datetime.date(2026, 8, 2),
            quantity=7,
        ),
        DailySales(
            product_id=product_b.id,
            date=datetime.date(2026, 8, 1),
            quantity=5,
        ),
        DailySales(
            product_id=july_product.id,
            date=datetime.date(2026, 7, 1),
            quantity=20,
        ),
        DailySales(
            product_id=previous_year_product.id,
            date=datetime.date(2025, 8, 1),
            quantity=30,
        ),
    ])
    db.session.commit()

    return {
        "product_a_id": product_a.id,
        "product_b_id": product_b.id,
    }


@pytest.fixture()
def cross_dataset_dashboard_records(flask_app, admin_dataset):
    now = datetime.datetime.now(datetime.timezone.utc)
    guest_dataset = Dataset(
        kind="guest",
        system_key=None,
        created_at=now,
        last_activity_at=now,
        absolute_expires_at=now + datetime.timedelta(hours=2),
    )
    admin_unique_product = Product(
        dataset=admin_dataset,
        year=2026,
        month=8,
        name="管理者限定商品",
        price=100,
    )
    admin_same_name_product = Product(
        dataset=admin_dataset,
        year=2026,
        month=8,
        name="クロワッサン",
        price=200,
    )
    guest_unique_product = Product(
        dataset=guest_dataset,
        year=2026,
        month=8,
        name="ゲスト限定商品",
        price=300,
    )
    guest_same_name_product = Product(
        dataset=guest_dataset,
        year=2026,
        month=8,
        name="クロワッサン",
        price=400,
    )
    db.session.add_all([
        guest_dataset,
        admin_unique_product,
        admin_same_name_product,
        guest_unique_product,
        guest_same_name_product,
    ])
    db.session.flush()
    db.session.add_all([
        DailySales(
            product_id=admin_unique_product.id,
            date=datetime.date(2026, 8, 1),
            quantity=11,
        ),
        DailySales(
            product_id=admin_same_name_product.id,
            date=datetime.date(2026, 8, 1),
            quantity=10,
        ),
        DailySales(
            product_id=guest_unique_product.id,
            date=datetime.date(2026, 8, 1),
            quantity=987654,
        ),
        DailySales(
            product_id=guest_same_name_product.id,
            date=datetime.date(2026, 8, 1),
            quantity=90,
        ),
    ])
    db.session.commit()


def test_admin_dashboard_excludes_guest_dataset_sales(
    authenticated_client,
    cross_dataset_dashboard_records,
):
    response = authenticated_client.get(
        "/dashboard?year=2026&month=8"
    )
    response_text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "管理者限定商品" in response_text
    assert "11" in response_text
    assert "ゲスト限定商品" not in response_text
    assert "987654" not in response_text


def test_admin_dashboard_does_not_combine_same_name_across_datasets(
    authenticated_client,
    cross_dataset_dashboard_records,
    monkeypatch,
):
    captured_ranked_sales = []
    real_render_template = app_module.render_template

    def capture_dashboard_context(template_name, *args, **kwargs):
        if template_name == "dashboard.html":
            captured_ranked_sales.extend(kwargs["ranked_sales"])
        return real_render_template(template_name, *args, **kwargs)

    monkeypatch.setattr(
        app_module,
        "render_template",
        capture_dashboard_context,
    )

    response = authenticated_client.get(
        "/dashboard?year=2026&month=8"
    )

    assert response.status_code == 200
    assert dict(captured_ranked_sales)["クロワッサン"] == 10


def test_admin_dashboard_api_excludes_guest_dataset_sales(
    authenticated_client,
    cross_dataset_dashboard_records,
):
    response = authenticated_client.get(
        "/api/dashboard-data?year=2026&month=8"
    )
    payload = response.get_json()
    ranked_sales = dict(payload["ranked_sales"])
    chart_sales = dict(zip(
        payload["chart_labels"],
        payload["chart_values"],
    ))

    assert response.status_code == 200
    assert ranked_sales["管理者限定商品"] == 11
    assert ranked_sales["クロワッサン"] == 10
    assert "ゲスト限定商品" not in ranked_sales
    assert 987654 not in payload["chart_values"]
    assert chart_sales["管理者限定商品"] == 11
    assert chart_sales["クロワッサン"] == 10
    assert "ゲスト限定商品" not in payload["chart_labels"]


def test_guest_a_dashboard_api_excludes_guest_b_dataset_sales(
    flask_app,
):
    now = datetime.datetime.now(datetime.timezone.utc)

    guest_a_dataset = Dataset(
        kind="guest",
        system_key=None,
        created_at=now,
        last_activity_at=now,
        absolute_expires_at=now + datetime.timedelta(hours=2),
    )
    guest_b_dataset = Dataset(
        kind="guest",
        system_key=None,
        created_at=now,
        last_activity_at=now,
        absolute_expires_at=now + datetime.timedelta(hours=2),
    )

    guest_a_unique_product = Product(
        dataset=guest_a_dataset,
        year=2026,
        month=8,
        name="Guest A限定商品",
        price=100,
    )
    guest_a_same_name_product = Product(
        dataset=guest_a_dataset,
        year=2026,
        month=8,
        name="クロワッサン",
        price=200,
    )
    guest_b_unique_product = Product(
        dataset=guest_b_dataset,
        year=2026,
        month=8,
        name="Guest B限定商品",
        price=300,
    )
    guest_b_same_name_product = Product(
        dataset=guest_b_dataset,
        year=2026,
        month=8,
        name="クロワッサン",
        price=400,
    )

    db.session.add_all([
        guest_a_dataset,
        guest_b_dataset,
        guest_a_unique_product,
        guest_a_same_name_product,
        guest_b_unique_product,
        guest_b_same_name_product,
    ])
    db.session.flush()

    db.session.add_all([
        DailySales(
            product_id=guest_a_unique_product.id,
            date=datetime.date(2026, 8, 1),
            quantity=11,
        ),
        DailySales(
            product_id=guest_a_same_name_product.id,
            date=datetime.date(2026, 8, 1),
            quantity=10,
        ),
        DailySales(
            product_id=guest_b_unique_product.id,
            date=datetime.date(2026, 8, 1),
            quantity=987654,
        ),
        DailySales(
            product_id=guest_b_same_name_product.id,
            date=datetime.date(2026, 8, 1),
            quantity=90,
        ),
    ])
    db.session.commit()

    guest_a_client = flask_app.test_client()
    with guest_a_client.session_transaction() as session_data:
        session_data["_user_id"] = f"guest:{guest_a_dataset.id}"
        session_data["_fresh"] = True

    response = guest_a_client.get(
        "/api/dashboard-data?year=2026&month=8"
    )

    payload = response.get_json()
    ranked_sales = dict(payload["ranked_sales"])
    chart_sales = dict(zip(
        payload["chart_labels"],
        payload["chart_values"],
    ))

    assert response.status_code == 200

    assert ranked_sales["Guest A限定商品"] == 11
    assert ranked_sales["クロワッサン"] == 10
    assert "Guest B限定商品" not in ranked_sales
    assert 987654 not in payload["chart_values"]

    assert chart_sales["Guest A限定商品"] == 11
    assert chart_sales["クロワッサン"] == 10
    assert "Guest B限定商品" not in payload["chart_labels"]


def test_guest_a_dashboard_html_excludes_guest_b_dataset_sales(
    flask_app,
    monkeypatch,
):
    now = datetime.datetime.now(datetime.timezone.utc)

    guest_a_dataset = Dataset(
        kind="guest",
        system_key=None,
        created_at=now,
        last_activity_at=now,
        absolute_expires_at=now + datetime.timedelta(hours=2),
    )
    guest_b_dataset = Dataset(
        kind="guest",
        system_key=None,
        created_at=now,
        last_activity_at=now,
        absolute_expires_at=now + datetime.timedelta(hours=2),
    )

    guest_a_unique_product = Product(
        dataset=guest_a_dataset,
        year=2026,
        month=8,
        name="Guest A限定商品",
        price=100,
    )
    guest_a_same_name_product = Product(
        dataset=guest_a_dataset,
        year=2026,
        month=8,
        name="クロワッサン",
        price=200,
    )
    guest_b_unique_product = Product(
        dataset=guest_b_dataset,
        year=2026,
        month=8,
        name="Guest B限定商品",
        price=300,
    )
    guest_b_same_name_product = Product(
        dataset=guest_b_dataset,
        year=2026,
        month=8,
        name="クロワッサン",
        price=400,
    )

    db.session.add_all([
        guest_a_dataset,
        guest_b_dataset,
        guest_a_unique_product,
        guest_a_same_name_product,
        guest_b_unique_product,
        guest_b_same_name_product,
    ])
    db.session.flush()

    db.session.add_all([
        DailySales(
            product_id=guest_a_unique_product.id,
            date=datetime.date(2026, 8, 1),
            quantity=11,
        ),
        DailySales(
            product_id=guest_a_same_name_product.id,
            date=datetime.date(2026, 8, 1),
            quantity=10,
        ),
        DailySales(
            product_id=guest_b_unique_product.id,
            date=datetime.date(2026, 8, 1),
            quantity=987654,
        ),
        DailySales(
            product_id=guest_b_same_name_product.id,
            date=datetime.date(2026, 8, 1),
            quantity=90,
        ),
    ])
    db.session.commit()

    captured_ranked_sales = []
    real_render_template = app_module.render_template

    def capture_dashboard_context(template_name, *args, **kwargs):
        if template_name == "dashboard.html":
            captured_ranked_sales.extend(kwargs["ranked_sales"])
        return real_render_template(template_name, *args, **kwargs)

    monkeypatch.setattr(
        app_module,
        "render_template",
        capture_dashboard_context,
    )

    guest_a_client = flask_app.test_client()
    with guest_a_client.session_transaction() as session_data:
        session_data["_user_id"] = f"guest:{guest_a_dataset.id}"
        session_data["_fresh"] = True

    response = guest_a_client.get(
        "/dashboard?year=2026&month=8"
    )
    response_text = response.get_data(as_text=True)
    ranked_sales = dict(captured_ranked_sales)

    assert response.status_code == 200

    assert ranked_sales["Guest A限定商品"] == 11
    assert ranked_sales["クロワッサン"] == 10
    assert "Guest B限定商品" not in ranked_sales

    assert "Guest A限定商品" in response_text
    assert "Guest B限定商品" not in response_text
    assert "987654" not in response_text


def test_dashboard_api_returns_sales_aggregation_for_selected_period(
    authenticated_client,
    dashboard_records,
):
    products_before = _product_snapshot()
    sales_before = _sales_snapshot()

    response = authenticated_client.get(
        "/api/dashboard-data?year=2026&month=8"
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert response.is_json
    assert set(payload) == {
        "ranked_sales",
        "chart_labels",
        "chart_values",
        "ai_advice",
        "period_text",
    }
    assert payload["ranked_sales"] == [
        ["商品A", 10],
        ["商品B", 5],
    ]
    assert payload["ai_advice"] == FIXED_AI_ADVICE
    assert payload["period_text"] == "8月度"
    assert _product_snapshot() == products_before
    assert _sales_snapshot() == sales_before


def test_dashboard_api_chart_matches_ranked_sales(
    authenticated_client,
    dashboard_records,
):
    response = authenticated_client.get(
        "/api/dashboard-data?year=2026&month=8"
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ranked_sales"] == [
        ["商品A", 10],
        ["商品B", 5],
    ]
    assert payload["chart_labels"] == ["商品A", "商品B"]
    assert payload["chart_values"] == [10, 5]
    assert [item[0] for item in payload["ranked_sales"]] == payload[
        "chart_labels"
    ]
    assert [item[1] for item in payload["ranked_sales"]] == payload[
        "chart_values"
    ]


def test_dashboard_api_keeps_historical_sales_for_inactive_products(
    authenticated_client,
    dashboard_records,
):
    product_b = db.session.get(Product, dashboard_records["product_b_id"])
    product_b.is_active = False
    db.session.commit()
    products_before = _product_snapshot()
    sales_before = _sales_snapshot()

    response = authenticated_client.get(
        "/api/dashboard-data?year=2026&month=8"
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert ["商品B", 5] in payload["ranked_sales"]
    assert db.session.get(Product, product_b.id).is_active is False
    assert _product_snapshot() == products_before
    assert _sales_snapshot() == sales_before


def test_dashboard_api_returns_all_periods_without_filters(
    authenticated_client,
    dashboard_records,
):
    response = authenticated_client.get("/api/dashboard-data")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ranked_sales"] == [
        ["前年商品", 30],
        ["7月商品", 20],
        ["商品A", 10],
        ["商品B", 5],
    ]
    assert payload["chart_labels"] == [
        "前年商品",
        "7月商品",
        "商品A",
        "商品B",
    ]
    assert payload["chart_values"] == [30, 20, 10, 5]
    assert payload["period_text"] == "全期間"


@pytest.mark.parametrize(
    ("route", "invalid_query"),
    [
        pytest.param("/dashboard", "year=abc", id="dashboard-invalid-year"),
        pytest.param("/dashboard", "month=abc", id="dashboard-invalid-month"),
        pytest.param(
            "/api/dashboard-data",
            "year=abc",
            id="dashboard-api-invalid-year",
        ),
        pytest.param(
            "/api/dashboard-data",
            "month=abc",
            id="dashboard-api-invalid-month",
        ),
        pytest.param(
            "/api/ai-advice",
            "year=abc",
            id="ai-advice-api-invalid-year",
        ),
        pytest.param(
            "/api/ai-advice",
            "month=abc",
            id="ai-advice-api-invalid-month",
        ),
    ],
)
def test_dashboard_routes_reject_noninteger_query_with_bad_request(
    authenticated_client,
    flask_app,
    monkeypatch,
    route,
    invalid_query,
):
    gemini_client = Mock()
    monkeypatch.setattr(app_module.genai, "Client", gemini_client)
    monkeypatch.setitem(flask_app.config, "PROPAGATE_EXCEPTIONS", False)

    response = authenticated_client.get(f"{route}?{invalid_query}")

    if route == "/api/ai-advice":
        assert response.status_code == 400 and not gemini_client.called, (
            f"status={response.status_code}, "
            f"gemini_calls={gemini_client.call_count}"
        )
    else:
        assert response.status_code == 400
