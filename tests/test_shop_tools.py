import datetime
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import app as app_module
from models import Dataset, db


SHOP_TOOL_ROUTES = {
    "orders": "/material-orders",
    "memo": "/shop-tools/memo",
    "tasks": "/shop-tools/tasks",
}


def _guest_client(flask_app):
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

    test_client = flask_app.test_client()
    with test_client.session_transaction() as session_data:
        session_data["_user_id"] = f"guest:{dataset.id}"
        session_data["_fresh"] = True
    return test_client


def _assert_shop_tools_navigation(document, active_tool):
    navigation = document.select_one(
        'nav[aria-label="店舗メモツールのメニュー"]'
    )
    assert navigation is not None

    links = {
        link["data-tool"]: link
        for link in navigation.select("a[data-tool]")
    }
    assert set(links) == set(SHOP_TOOL_ROUTES)
    for tool, path in SHOP_TOOL_ROUTES.items():
        assert links[tool]["href"] == path
        if tool == active_tool:
            assert links[tool].get("aria-current") == "page"
            assert "shop-tools-nav-link-current" in links[tool].get(
                "class",
                [],
            )
        else:
            assert links[tool].get("aria-current") is None


def test_index_uses_sidebar_as_its_single_shop_tools_entry(
    authenticated_client,
    admin_dataset,
):
    response = authenticated_client.get("/")
    document = BeautifulSoup(response.get_data(as_text=True), "html.parser")
    links = document.select(
        'nav[aria-label="主要メニュー"] a[data-page="shop-tools"]'
    )

    assert response.status_code == 200
    assert len(links) == 1
    assert links[0]["href"] == "/material-orders"
    assert "店舗メモツール" in links[0].get_text()
    assert document.select("a.menu-link-shop-tools") == []


@pytest.mark.parametrize(
    ("active_tool", "route"),
    SHOP_TOOL_ROUTES.items(),
)
def test_admin_can_open_each_shop_tool_and_see_current_navigation(
    authenticated_client,
    admin_dataset,
    active_tool,
    route,
):
    response = authenticated_client.get(route)
    document = BeautifulSoup(response.get_data(as_text=True), "html.parser")

    assert response.status_code == 200
    assert "店舗メモツール" in document.get_text()
    assert "利用中：管理者" in document.get_text()
    _assert_shop_tools_navigation(document, active_tool)


@pytest.mark.parametrize(
    ("active_tool", "route"),
    SHOP_TOOL_ROUTES.items(),
)
def test_guest_can_open_each_shop_tool_and_see_current_navigation(
    flask_app,
    active_tool,
    route,
):
    guest_client = _guest_client(flask_app)
    response = guest_client.get(route)
    document = BeautifulSoup(response.get_data(as_text=True), "html.parser")

    assert response.status_code == 200
    assert "利用中：ゲストデモ" in document.get_text()
    _assert_shop_tools_navigation(document, active_tool)


@pytest.mark.parametrize("route", SHOP_TOOL_ROUTES.values())
def test_anonymous_user_cannot_open_shop_tools(client, route):
    response = client.get(route, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?")


def test_future_tools_are_explicit_read_only_placeholders(
    authenticated_client,
    admin_dataset,
):
    response = authenticated_client.get("/shop-tools/tasks")
    document = BeautifulSoup(response.get_data(as_text=True), "html.parser")

    assert response.status_code == 200
    assert document.select_one("h1").get_text(strip=True).endswith("タスク")
    assert "準備中" in document.get_text()
    assert document.select_one("main form") is None


def test_material_orders_shell_keeps_visible_form_and_accessible_fab(
    authenticated_client,
    admin_dataset,
):
    response = authenticated_client.get("/material-orders")
    document = BeautifulSoup(response.get_data(as_text=True), "html.parser")
    fab = document.select_one("button.shop-tools-fab")

    assert response.status_code == 200
    assert document.select_one('label[for="material-name"]') is not None
    assert document.select_one('label[for="material-quantity"]') is not None
    assert document.select_one('label[for="material-memo"]') is not None
    assert fab is not None
    assert fab.get("type") == "button"
    assert fab.get("aria-label") == "材料を追加"
    assert fab.get("aria-controls") == "material-name"
    assert "材料を追加" in document.select_one(
        ".shop-tools-fab-label"
    ).get_text()


def test_shop_tools_templates_keep_autoescape_and_safe_dom_updates():
    template_root = Path(app_module.app.root_path) / "templates"
    template_paths = [
        template_root / "material_orders.html",
        template_root / "shop_tools" / "base.html",
        template_root / "shop_tools" / "memos.html",
        template_root / "shop_tools" / "memo_trash.html",
        template_root / "shop_tools" / "_memo_search.html",
        template_root / "shop_tools" / "placeholder.html",
    ]

    for template_path in template_paths:
        source = template_path.read_text()
        assert "|safe" not in source
        assert "innerHTML" not in source


def test_shop_tools_css_preserves_tap_targets_and_keyboard_focus():
    css_source = (
        Path(app_module.app.root_path) / "static" / "style.css"
    ).read_text()

    assert ".shop-tools-nav-link" in css_source
    assert "min-height: 48px" in css_source
    assert ":focus-visible" in css_source
