from unittest.mock import Mock

import app as app_module


EXPECTED_MAX_CONTENT_LENGTH = 256 * 1024


def test_oversized_request_is_rejected_before_guest_business_logic(
    client,
    csrf_token,
    monkeypatch,
):
    assert (
        app_module.app.config["MAX_CONTENT_LENGTH"]
        == EXPECTED_MAX_CONTENT_LENGTH
    )
    reserve_attempt = Mock(return_value=False)
    monkeypatch.setattr(
        app_module,
        "_reserve_guest_creation_attempt",
        reserve_attempt,
    )
    token = csrf_token(client, "/login")

    response = client.post(
        "/guest/start",
        data={
            "csrf_token": token,
            "oversized_padding": "x" * (EXPECTED_MAX_CONTENT_LENGTH + 1),
        },
    )

    assert response.status_code == 413
    reserve_attempt.assert_not_called()


def test_oversized_ai_json_is_rejected_before_ai_business_logic(
    authenticated_client,
    admin_dataset,
    csrf_token,
    monkeypatch,
):
    generate_advice = Mock()
    monkeypatch.setattr(
        app_module,
        "_generate_ai_advice",
        generate_advice,
    )
    token = csrf_token(authenticated_client, "/login")

    response = authenticated_client.post(
        "/api/ai-advice",
        headers={"X-CSRFToken": token},
        json={
            "oversized_padding": "x" * (EXPECTED_MAX_CONTENT_LENGTH + 1),
        },
    )

    assert response.status_code == 413
    generate_advice.assert_not_called()
