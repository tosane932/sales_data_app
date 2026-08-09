import datetime
import re
from pathlib import Path

from bs4 import BeautifulSoup
from flask import render_template

from app import app as flask_app


PROJECT_ROOT = Path(__file__).resolve().parent
DASHBOARD_TEMPLATE = PROJECT_ROOT / "templates" / "dashboard.html"
INPUT_TEMPLATE = PROJECT_ROOT / "templates" / "input.html"


def _read_template(path):
    return path.read_text(encoding="utf-8")


def _source_between(source, start_marker, end_marker):
    assert start_marker in source
    assert end_marker in source
    return source.split(start_marker, 1)[1].split(end_marker, 1)[0]


def test_dynamic_ranking_product_name_uses_text_dom_api():
    """商品名の動的表示がHTML sinkへ戻る事故を検知するsource guard。"""
    source = _read_template(DASHBOARD_TEMPLATE)
    update_source = _source_between(
        source,
        "function updateDashboard(event)",
        "function loadAiAdvice()"
    )

    assert re.search(
        r"productName\s*\.\s*textContent\s*=\s*item\[0\]",
        update_source
    )
    assert not re.search(
        r"rankContainer\s*\.\s*"
        r"(?:innerHTML|outerHTML|insertAdjacentHTML)\b",
        update_source
    )


def test_dashboard_ai_responses_use_text_dom_api():
    """dashboardのAI返答がinnerHTMLへ戻る事故を検知するsource guard。"""
    source = _read_template(DASHBOARD_TEMPLATE)
    update_source = _source_between(
        source,
        "function updateDashboard(event)",
        "function loadAiAdvice()"
    )
    advice_source = source.split("function loadAiAdvice()", 1)[1]

    assert re.search(
        r"setTextWithLineBreaks\(\s*aiText\s*,\s*data\.ai_advice\s*\)",
        update_source
    )
    assert re.search(
        r"setTextWithLineBreaks\(\s*aiText\s*,\s*data\.ai_advice\s*\)",
        advice_source
    )

    for target_source in (update_source, advice_source):
        assert not re.search(
            r"aiText\s*\.\s*"
            r"(?:innerHTML|outerHTML|insertAdjacentHTML)\b",
            target_source
        )


def test_input_ai_response_uses_text_dom_api():
    """inputのAI返答がinnerHTMLへ戻る事故を検知するsource guard。"""
    source = _read_template(INPUT_TEMPLATE)
    greeting_source = source.split("function loadGreeting()", 1)[1]

    assert re.search(
        r"setTextWithLineBreaks\("
        r"\s*greetingText\s*,\s*data\.message\s*\)",
        greeting_source
    )
    assert not re.search(
        r"greetingText\s*\.\s*"
        r"(?:innerHTML|outerHTML|insertAdjacentHTML)\b",
        greeting_source
    )


def test_dashboard_initial_ai_binding_does_not_disable_autoescape():
    """初期AI表示へJinjaのsafe指定が戻る事故を検知するsource guard。"""
    source = _read_template(DASHBOARD_TEMPLATE)
    binding = re.search(
        r'<p\b[^>]*id="aiAdviceText"[^>]*>(.*?)</p>',
        source,
        re.DOTALL
    )

    assert binding is not None
    assert re.search(r"{{\s*ai_advice\s*}}", binding.group(1))
    assert not re.search(r"\|\s*safe\b", binding.group(1))


def test_dashboard_initial_ai_advice_autoescapes_html_like_text():
    """HTML風のAI文字列が、初期表示で要素として解釈されないことを確認する。"""
    ai_advice = "<b>テスト</b>\n<img src=x>"

    with flask_app.test_request_context("/dashboard"):
        html = render_template(
            "dashboard.html",
            ranked_sales=[],
            chart_labels=[],
            chart_values=[],
            ai_advice=ai_advice,
            year=2026,
            month=8,
            now=datetime.date(2026, 8, 9)
        )

    soup = BeautifulSoup(html, "html.parser")
    ai_element = soup.select_one("#aiAdviceText")

    assert ai_element is not None
    assert ai_element.get_text() == ai_advice
    assert ai_element.select("b, img") == []
    assert "&lt;b&gt;テスト&lt;/b&gt;" in html
    assert "&lt;img src=x&gt;" in html
