from types import SimpleNamespace
from unittest.mock import Mock

import app as app_module
import config
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
