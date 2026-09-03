import datetime
from types import SimpleNamespace
from unittest.mock import Mock

from flask import g
from sqlalchemy.exc import SQLAlchemyError

import app as app_module
from models import DailySales, Dataset, Product, db


LIMIT_RESPONSE = {
    "error": "guest_ai_limit_reached",
    "message": "ゲストデモで利用できるAI機能は合計3回までです。",
    "limit": 3,
    "remaining": 0,
}


def _create_guest_dataset():
    now = datetime.datetime.now(datetime.timezone.utc)
    dataset = Dataset(
        kind="guest",
        system_key=None,
        created_at=now,
        last_activity_at=now,
        absolute_expires_at=now + datetime.timedelta(hours=2),
    )
    db.session.add(dataset)
    db.session.commit()
    return dataset


def _create_guest_sales(dataset):
    product = Product(
        dataset=dataset,
        year=2026,
        month=9,
        name="AI制限テスト商品",
        price=320,
        is_active=True,
    )
    db.session.add(product)
    db.session.flush()
    sale = DailySales(
        product_id=product.id,
        date=datetime.date(2026, 9, 3),
        quantity=12,
    )
    db.session.add(sale)
    db.session.commit()
    return product, sale


def _guest_client(flask_app, dataset):
    client = flask_app.test_client()
    with client.session_transaction() as session_data:
        session_data["_user_id"] = f"guest:{dataset.id}"
        session_data["_fresh"] = True
    return client


def _mock_gemini(monkeypatch, *, side_effect=None):
    if side_effect is None:
        generate_content = Mock(
            return_value=SimpleNamespace(text="モックされたGemini応答")
        )
    else:
        generate_content = Mock(side_effect=side_effect)

    fake_client = SimpleNamespace(
        models=SimpleNamespace(generate_content=generate_content)
    )
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-test-key")
    monkeypatch.setattr(
        app_module.genai,
        "Client",
        Mock(return_value=fake_client),
    )
    return generate_content


def _advice(client):
    return client.get("/api/ai-advice?year=2026&month=9")


def _usage_count(dataset_id):
    db.session.expire_all()
    return db.session.get(Dataset, dataset_id).guest_ai_usage_count


def test_guest_can_use_ai_advice_three_times(flask_app, monkeypatch):
    guest_dataset = _create_guest_dataset()
    _create_guest_sales(guest_dataset)
    client = _guest_client(flask_app, guest_dataset)
    generate_content = _mock_gemini(monkeypatch)

    responses = [_advice(client) for _ in range(3)]

    assert [response.status_code for response in responses] == [200, 200, 200]
    assert generate_content.call_count == 3
    assert _usage_count(guest_dataset.id) == 3


def test_guest_fourth_ai_advice_is_429_without_gemini_call(
    flask_app,
    monkeypatch,
):
    guest_dataset = _create_guest_dataset()
    _create_guest_sales(guest_dataset)
    client = _guest_client(flask_app, guest_dataset)
    generate_content = _mock_gemini(monkeypatch)

    responses = [_advice(client) for _ in range(4)]

    assert [response.status_code for response in responses] == [
        200,
        200,
        200,
        429,
    ]
    assert responses[-1].get_json() == LIMIT_RESPONSE
    assert generate_content.call_count == 3
    assert _usage_count(guest_dataset.id) == 3


def test_guest_greeting_and_advice_share_three_use_limit(
    flask_app,
    monkeypatch,
):
    guest_dataset = _create_guest_dataset()
    _create_guest_sales(guest_dataset)
    client = _guest_client(flask_app, guest_dataset)
    generate_content = _mock_gemini(monkeypatch)

    responses = [
        client.get("/api/greeting"),
        client.get("/api/greeting"),
        _advice(client),
        client.get("/api/greeting"),
    ]

    assert [response.status_code for response in responses] == [
        200,
        200,
        200,
        429,
    ]
    assert responses[-1].get_json() == LIMIT_RESPONSE
    assert generate_content.call_count == 3
    assert _usage_count(guest_dataset.id) == 3


def test_three_guest_advice_calls_block_greeting(flask_app, monkeypatch):
    guest_dataset = _create_guest_dataset()
    _create_guest_sales(guest_dataset)
    client = _guest_client(flask_app, guest_dataset)
    generate_content = _mock_gemini(monkeypatch)

    advice_responses = [_advice(client) for _ in range(3)]
    greeting_response = client.get("/api/greeting")

    assert all(response.status_code == 200 for response in advice_responses)
    assert greeting_response.status_code == 429
    assert greeting_response.get_json() == LIMIT_RESPONSE
    assert generate_content.call_count == 3


def test_guest_a_limit_does_not_affect_guest_b(flask_app, monkeypatch):
    guest_a = _create_guest_dataset()
    guest_b = _create_guest_dataset()
    client_a = _guest_client(flask_app, guest_a)
    client_b = _guest_client(flask_app, guest_b)
    generate_content = _mock_gemini(monkeypatch)

    guest_a_responses = [client_a.get("/api/greeting") for _ in range(4)]
    g.pop("_login_user", None)
    guest_b_response = client_b.get("/api/greeting")

    assert [response.status_code for response in guest_a_responses] == [
        200,
        200,
        200,
        429,
    ]
    assert guest_b_response.status_code == 200
    assert generate_content.call_count == 4
    assert _usage_count(guest_a.id) == 3
    assert _usage_count(guest_b.id) == 1


def test_guest_usage_does_not_change_other_guest_or_admin_dataset(
    flask_app,
    admin_dataset,
    monkeypatch,
):
    guest_a = _create_guest_dataset()
    guest_b = _create_guest_dataset()
    client_a = _guest_client(flask_app, guest_a)
    _mock_gemini(monkeypatch)

    response = client_a.get("/api/greeting")

    assert response.status_code == 200
    assert _usage_count(guest_a.id) == 1
    assert _usage_count(guest_b.id) == 0
    assert _usage_count(admin_dataset.id) == 0


def test_guest_cannot_reset_ai_limit_with_session_values(
    flask_app,
    admin_dataset,
    monkeypatch,
):
    guest_dataset = _create_guest_dataset()
    client = _guest_client(flask_app, guest_dataset)
    generate_content = _mock_gemini(monkeypatch)

    for _ in range(3):
        assert client.get("/api/greeting").status_code == 200

    with client.session_transaction() as session_data:
        session_data["guest_ai_usage_count"] = 0
        session_data["dataset_id"] = str(admin_dataset.id)
        session_data["role"] = "admin"
        session_data["is_admin"] = True

    response = client.get("/api/greeting")

    assert response.status_code == 429
    assert response.get_json() == LIMIT_RESPONSE
    assert generate_content.call_count == 3
    assert _usage_count(guest_dataset.id) == 3
    assert _usage_count(admin_dataset.id) == 0


def test_admin_can_use_ai_more_than_three_times(
    authenticated_client,
    admin_dataset,
    monkeypatch,
):
    generate_content = _mock_gemini(monkeypatch)

    responses = [authenticated_client.get("/api/greeting") for _ in range(5)]

    assert all(response.status_code == 200 for response in responses)
    assert generate_content.call_count == 5


def test_admin_ai_usage_keeps_guest_counter_zero(
    authenticated_client,
    admin_dataset,
    monkeypatch,
):
    _mock_gemini(monkeypatch)

    for _ in range(4):
        assert authenticated_client.get("/api/greeting").status_code == 200

    assert _usage_count(admin_dataset.id) == 0


def test_ai_limit_rejection_does_not_change_product_or_sales(
    flask_app,
    monkeypatch,
):
    guest_dataset = _create_guest_dataset()
    product, sale = _create_guest_sales(guest_dataset)
    product_snapshot = (
        product.dataset_id,
        product.name,
        product.price,
        product.is_active,
    )
    sale_snapshot = (sale.product_id, sale.date, sale.quantity)
    client = _guest_client(flask_app, guest_dataset)
    generate_content = _mock_gemini(monkeypatch)

    responses = [_advice(client) for _ in range(4)]

    db.session.refresh(product)
    db.session.refresh(sale)
    assert responses[-1].status_code == 429
    assert generate_content.call_count == 3
    assert (
        product.dataset_id,
        product.name,
        product.price,
        product.is_active,
    ) == product_snapshot
    assert (sale.product_id, sale.date, sale.quantity) == sale_snapshot


def test_guest_ai_reservation_database_failure_does_not_call_gemini(
    flask_app,
    monkeypatch,
):
    guest_dataset = _create_guest_dataset()
    client = _guest_client(flask_app, guest_dataset)
    generate_content = _mock_gemini(monkeypatch)
    real_execute = db.session.execute

    def fail_conditional_update(statement, *args, **kwargs):
        if getattr(statement, "is_update", False):
            raise SQLAlchemyError("test Guest AI reservation failure")
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db.session, "execute", fail_conditional_update)

    response = client.get("/api/greeting")

    assert response.status_code == 503
    generate_content.assert_not_called()
    assert _usage_count(guest_dataset.id) == 0


def test_gemini_failure_does_not_refund_guest_usage(flask_app, monkeypatch):
    guest_dataset = _create_guest_dataset()
    client = _guest_client(flask_app, guest_dataset)
    generate_content = _mock_gemini(
        monkeypatch,
        side_effect=RuntimeError("test Gemini failure"),
    )

    response = client.get("/api/greeting")

    assert response.status_code == 200
    generate_content.assert_called_once()
    assert _usage_count(guest_dataset.id) == 1


def test_missing_api_key_does_not_consume_guest_usage(
    flask_app,
    monkeypatch,
):
    guest_dataset = _create_guest_dataset()
    client = _guest_client(flask_app, guest_dataset)
    client_factory = Mock()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(app_module.genai, "Client", client_factory)

    response = client.get("/api/greeting")

    assert response.status_code == 200
    client_factory.assert_not_called()
    assert _usage_count(guest_dataset.id) == 0


def test_empty_sales_advice_does_not_consume_guest_usage(
    flask_app,
    monkeypatch,
):
    guest_dataset = _create_guest_dataset()
    client = _guest_client(flask_app, guest_dataset)
    generate_content = _mock_gemini(monkeypatch)

    response = _advice(client)

    assert response.status_code == 200
    generate_content.assert_not_called()
    assert _usage_count(guest_dataset.id) == 0
