from prompts import build_sales_prompt


def test_build_sales_prompt_places_sales_data_in_its_section():
    """販売データが、専用セクションへそのまま埋め込まれることを確認する。"""
    sample_data = "月曜日: メロンパン50個、あんぱん30個"
    result = build_sales_prompt(sample_data)

    sales_section = result.split("【販売データ】", 1)[1].split(
        "【分析の観点】",
        1
    )[0]

    assert sample_data in sales_section


def test_build_sales_prompt_preserves_role_and_analysis_contract():
    """AIの役割と、必要な分析観点が欠落していないことを確認する。"""
    result = build_sales_prompt("テストデータ")

    required_contracts = [
        "食品業界のトレンドと消費者行動に精通した",
        "経営コンサルタント",
        "近年の食トレンドや競合業態の動向",
        "ターゲット層別のニーズ",
        "SNSでの訴求可能性",
        "曜日・季節・祝日パターン",
    ]

    for contract in required_contracts:
        assert contract in result


def test_build_sales_prompt_preserves_output_contract():
    """提案数、箇条書き、回答の長さに関する契約を確認する。"""
    result = build_sales_prompt("テストデータ")

    assert "提案を3点挙げて" in result
    assert "箇条書き3点" in result
    assert "各1〜2文" in result
    assert "簡潔に" in result
