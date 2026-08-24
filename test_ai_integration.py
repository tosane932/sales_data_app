import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.genai import errors

import app as app_module
import config
from models import DailySales, Dataset, Product, db
from prompts import build_sales_prompt


def test_generate_ai_advice_sends_complete_sales_prompt(monkeypatch):
    """販売プロンプト、モデル、応答本文のGemini接続契約を確認する。"""
    generate_content = Mock(
        return_value=SimpleNamespace(text="モックされたAIアドバイス")
    )
    fake_client = SimpleNamespace(
        models=SimpleNamespace(generate_content=generate_content)
    )
    client_factory = Mock(return_value=fake_client)

    monkeypatch.setenv("GEMINI_API_KEY", "dummy-test-key")
    monkeypatch.setattr(app_module.genai, "Client", client_factory)

    ranked_sales = [
        ("メロンパン", 12),
        ("あんぱん", 7),
    ]
    result = app_module._generate_ai_advice(ranked_sales)

    expected_prompt = build_sales_prompt(
        "メロンパン: 12個, あんぱん: 7個"
    )

    client_factory.assert_called_once_with(api_key="dummy-test-key")
    generate_content.assert_called_once_with(
        model=config.GEMINI_MODEL,
        contents=expected_prompt
    )
    assert result == "モックされたAIアドバイス"


def test_authenticated_ai_advice_api_returns_generated_advice_from_filtered_sales(
    authenticated_client,
    admin_dataset,
    monkeypatch,
):
    august_product_a = Product(
        dataset=admin_dataset,
        year=2026,
        month=8,
        name="8月商品A",
        price=100,
    )
    august_product_b = Product(
        dataset=admin_dataset,
        year=2026,
        month=8,
        name="8月商品B",
        price=200,
    )
    july_product = Product(
        dataset=admin_dataset,
        year=2026,
        month=7,
        name="7月対象外商品",
        price=300,
    )
    previous_year_august_product = Product(
        dataset=admin_dataset,
        year=2025,
        month=8,
        name="前年8月対象外商品",
        price=400,
    )
    db.session.add_all([
        august_product_a,
        august_product_b,
        july_product,
        previous_year_august_product,
    ])
    db.session.flush()
    db.session.add_all([
        DailySales(
            product_id=august_product_a.id,
            date=datetime.date(2026, 8, 1),
            quantity=10,
        ),
        DailySales(
            product_id=august_product_b.id,
            date=datetime.date(2026, 8, 1),
            quantity=5,
        ),
        DailySales(
            product_id=july_product.id,
            date=datetime.date(2026, 7, 1),
            quantity=99,
        ),
        DailySales(
            product_id=previous_year_august_product.id,
            date=datetime.date(2025, 8, 1),
            quantity=77,
        ),
    ])
    db.session.commit()

    generate_content = Mock(
        return_value=SimpleNamespace(text="モックされたAIアドバイス")
    )
    fake_client = SimpleNamespace(
        models=SimpleNamespace(generate_content=generate_content)
    )
    client_factory = Mock(return_value=fake_client)
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-test-key")
    monkeypatch.setattr(app_module.genai, "Client", client_factory)

    response = authenticated_client.get(
        "/api/ai-advice?year=2026&month=8"
    )

    assert response.status_code == 200
    assert response.is_json
    assert response.get_json() == {
        "ai_advice": "モックされたAIアドバイス"
    }
    client_factory.assert_called_once_with(api_key="dummy-test-key")
    generate_content.assert_called_once()
    contents = generate_content.call_args.kwargs["contents"]
    assert "8月商品A: 10個" in contents
    assert "8月商品B: 5個" in contents
    assert "7月対象外商品" not in contents
    assert "前年8月対象外商品" not in contents


def test_admin_ai_advice_prompt_excludes_guest_dataset_sales(
    authenticated_client,
    admin_dataset,
    monkeypatch,
):
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
        name="AI管理者限定商品",
        price=100,
    )
    admin_same_name_product = Product(
        dataset=admin_dataset,
        year=2026,
        month=8,
        name="AI共通クロワッサン",
        price=200,
    )
    guest_unique_product = Product(
        dataset=guest_dataset,
        year=2026,
        month=8,
        name="AIゲスト機密商品",
        price=300,
    )
    guest_same_name_product = Product(
        dataset=guest_dataset,
        year=2026,
        month=8,
        name="AI共通クロワッサン",
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
            quantity=12,
        ),
        DailySales(
            product_id=admin_same_name_product.id,
            date=datetime.date(2026, 8, 1),
            quantity=10,
        ),
        DailySales(
            product_id=guest_unique_product.id,
            date=datetime.date(2026, 8, 1),
            quantity=876543,
        ),
        DailySales(
            product_id=guest_same_name_product.id,
            date=datetime.date(2026, 8, 1),
            quantity=90,
        ),
    ])
    db.session.commit()

    generate_content = Mock(
        return_value=SimpleNamespace(text="モックされたAIアドバイス")
    )
    fake_client = SimpleNamespace(
        models=SimpleNamespace(generate_content=generate_content)
    )
    client_factory = Mock(return_value=fake_client)
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-test-key")
    monkeypatch.setattr(app_module.genai, "Client", client_factory)

    response = authenticated_client.get(
        "/api/ai-advice?year=2026&month=8"
    )

    assert response.status_code == 200
    generate_content.assert_called_once()
    contents = generate_content.call_args.kwargs["contents"]
    assert "AI管理者限定商品: 12個" in contents
    assert "AIゲスト機密商品" not in contents
    assert "876543" not in contents
    assert "AI共通クロワッサン: 10個" in contents
    assert "AI共通クロワッサン: 100個" not in contents


def test_guest_a_ai_advice_prompt_excludes_guest_b_dataset_sales(
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
        name="AI Guest A限定商品",
        price=100,
    )
    guest_a_same_name_product = Product(
        dataset=guest_a_dataset,
        year=2026,
        month=8,
        name="AI共通クロワッサン",
        price=200,
    )
    guest_b_unique_product = Product(
        dataset=guest_b_dataset,
        year=2026,
        month=8,
        name="AI Guest B機密商品",
        price=300,
    )
    guest_b_same_name_product = Product(
        dataset=guest_b_dataset,
        year=2026,
        month=8,
        name="AI共通クロワッサン",
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
            quantity=12,
        ),
        DailySales(
            product_id=guest_a_same_name_product.id,
            date=datetime.date(2026, 8, 1),
            quantity=10,
        ),
        DailySales(
            product_id=guest_b_unique_product.id,
            date=datetime.date(2026, 8, 1),
            quantity=876543,
        ),
        DailySales(
            product_id=guest_b_same_name_product.id,
            date=datetime.date(2026, 8, 1),
            quantity=90,
        ),
    ])
    db.session.commit()

    generate_content = Mock(
        return_value=SimpleNamespace(text="モックされたAIアドバイス")
    )
    fake_client = SimpleNamespace(
        models=SimpleNamespace(generate_content=generate_content)
    )
    client_factory = Mock(return_value=fake_client)

    monkeypatch.setenv("GEMINI_API_KEY", "dummy-test-key")
    monkeypatch.setattr(app_module.genai, "Client", client_factory)

    guest_a_client = flask_app.test_client()
    with guest_a_client.session_transaction() as session_data:
        session_data["_user_id"] = f"guest:{guest_a_dataset.id}"
        session_data["_fresh"] = True

    response = guest_a_client.get(
        "/api/ai-advice?year=2026&month=8"
    )

    assert response.status_code == 200
    generate_content.assert_called_once()

    contents = generate_content.call_args.kwargs["contents"]

    assert "AI Guest A限定商品: 12個" in contents
    assert "AI Guest B機密商品" not in contents
    assert "876543" not in contents

    assert "AI共通クロワッサン: 10個" in contents
    assert "AI共通クロワッサン: 100個" not in contents


@pytest.mark.parametrize(
    ("raised_error", "expected_message"),
    [
        pytest.param(
            errors.ClientError(
                429,
                {
                    "error": {
                        "code": 429,
                        "status": "RESOURCE_EXHAUSTED",
                        "message": "test rate limit",
                    }
                },
            ),
            (
                "☕【AIが少し休憩中です】\n"
                "短時間に多くの分析を行ったため、"
                "AIの利用制限がかかりました。"
                "少し時間を置いてから、もう一度お試しください。"
            ),
            id="429",
        ),
        pytest.param(
            errors.ServerError(
                503,
                {
                    "error": {
                        "code": 503,
                        "status": "UNAVAILABLE",
                        "message": "test service unavailable",
                    }
                },
            ),
            (
                "🥐【AIアシスタントが混み合っています】\n"
                "売上データは正常に保存・集計されています。"
                "少し時間を置いてから、もう一度お試しください。"
            ),
            id="503",
        ),
        pytest.param(
            RuntimeError("test unexpected failure"),
            (
                "🚨 AIアドバイスの生成中に一時的なエラーが発生しました。"
                "時間を置いてから、もう一度お試しください。"
            ),
            id="generic-error",
        ),
    ],
)
def test_generate_ai_advice_returns_fallback_when_gemini_fails(
    monkeypatch,
    raised_error,
    expected_message,
):
    generate_content = Mock(side_effect=raised_error)
    fake_client = SimpleNamespace(
        models=SimpleNamespace(generate_content=generate_content)
    )
    client_factory = Mock(return_value=fake_client)
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-test-key")
    monkeypatch.setattr(app_module.genai, "Client", client_factory)

    result = app_module._generate_ai_advice([("テスト商品", 1)])

    client_factory.assert_called_once_with(api_key="dummy-test-key")
    generate_content.assert_called_once()
    assert result == expected_message
