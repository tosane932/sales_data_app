# test_prompts.py
from prompts import build_sales_prompt

def test_build_sales_prompt_includes_sales_summary():
    """渡した販売データが、プロンプトの中にちゃんと埋め込まれているか確認"""
    sample_data = "月曜日: メロンパン50個、あんぱん30個"
    result = build_sales_prompt(sample_data)

    assert sample_data in result

def test_build_sales_prompt_includes_key_instructions():
    """出力形式の指示（3点・箇条書き）が、文脈ごと正しく含まれているか確認"""
    result = build_sales_prompt("テストデータ")

    # 「3点」という文字列の有無だけでなく、前後の文脈も含めて確認する
    assert "提案を3点挙げて" in result
    assert "箇条書き3点" in result

def test_build_sales_prompt_returns_string():
    """戻り値がちゃんと文字列型になっているか確認"""
    result = build_sales_prompt("テストデータ")

    assert isinstance(result, str)