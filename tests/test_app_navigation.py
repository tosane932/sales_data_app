import datetime
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import app as app_module
from models import Dataset, db


APP_ROUTES = {
    "products": "/",
    "sales": "/input",
    "dashboard": "/dashboard",
    "shop-tools": "/material-orders",
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


def _assert_app_navigation(document, active_page, user_label):
    sidebar = document.select_one("#app-sidebar")
    assert sidebar is not None
    navigation = sidebar.select_one('nav[aria-label="主要メニュー"]')
    assert navigation is not None

    links = {
        link["data-page"]: link
        for link in navigation.select("a[data-page]")
    }
    assert set(links) == set(APP_ROUTES)
    for page, path in APP_ROUTES.items():
        assert links[page]["href"] == path
        if page == active_page:
            assert links[page].get("aria-current") == "page"
            assert "app-sidebar-link-current" in links[page].get(
                "class",
                [],
            )
        else:
            assert links[page].get("aria-current") is None

    assert user_label in sidebar.get_text()
    logout_form = sidebar.select_one('form[action="/logout"]')
    assert logout_form is not None
    assert logout_form.get("method", "").lower() == "post"
    assert logout_form.select_one('input[name="csrf_token"]') is not None


@pytest.mark.parametrize(
    ("active_page", "route"),
    APP_ROUTES.items(),
)
def test_admin_sees_shared_sidebar_with_correct_current_page(
    authenticated_client,
    admin_dataset,
    active_page,
    route,
):
    response = authenticated_client.get(route)
    document = BeautifulSoup(response.get_data(as_text=True), "html.parser")

    assert response.status_code == 200
    _assert_app_navigation(document, active_page, "利用中：管理者")
    logout_button = document.select_one(
        "#app-sidebar .app-sidebar-logout-button"
    )
    assert "ログアウト" in logout_button.get_text()
    assert "ゲストを終了する" not in document.get_text()
    assert "ゲスト利用は継続します" not in document.get_text()


@pytest.mark.parametrize(
    ("active_page", "route"),
    APP_ROUTES.items(),
)
def test_guest_sees_shared_sidebar_with_correct_current_page(
    flask_app,
    active_page,
    route,
):
    guest_client = _guest_client(flask_app)
    response = guest_client.get(route)
    document = BeautifulSoup(response.get_data(as_text=True), "html.parser")

    assert response.status_code == 200
    _assert_app_navigation(document, active_page, "利用中：ゲストデモ")
    login_link = document.select_one("#app-sidebar a.app-sidebar-login-link")
    assert login_link is not None
    assert login_link["href"] == "/login"
    assert "ログイン画面に戻る" in login_link.get_text()
    assert "管理者ログイン画面を開く" not in login_link.get_text()
    assert "ゲスト利用は継続します" in login_link.get_text()
    logout_button = document.select_one(
        "#app-sidebar .app-sidebar-logout-button"
    )
    assert "ゲストを終了する" in logout_button.get_text()
    assert "ログアウト" not in logout_button.get_text()
    assert "touch-control-no-select" in logout_button.get("class", [])


def test_guest_returning_to_login_page_keeps_guest_session(flask_app):
    guest_client = _guest_client(flask_app)

    login_response = guest_client.get("/login")
    protected_response = guest_client.get("/material-orders")

    assert login_response.status_code == 200
    assert protected_response.status_code == 200
    assert "利用中：ゲストデモ" in protected_response.get_data(as_text=True)


@pytest.mark.parametrize("route", APP_ROUTES.values())
def test_anonymous_users_remain_blocked_from_app_routes(client, route):
    response = client.get(route, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?")


def test_mobile_navigation_has_accessible_open_and_close_controls(
    authenticated_client,
    admin_dataset,
):
    response = authenticated_client.get("/")
    document = BeautifulSoup(response.get_data(as_text=True), "html.parser")
    open_button = document.select_one("button.app-navigation-open-button")
    close_button = document.select_one("button.app-navigation-close-button")
    overlay = document.select_one("button.app-navigation-overlay")

    assert open_button is not None
    assert open_button.get("type") == "button"
    assert open_button.get("aria-label") == "主要メニューを開く"
    assert open_button.get("aria-controls") == "app-sidebar"
    assert open_button.get("aria-expanded") == "false"
    assert open_button.get_text(strip=True) == "☰"
    assert "touch-control-no-select" in open_button.get("class", [])
    assert close_button is not None
    assert close_button.get("aria-label") == "主要メニューを閉じる"
    assert "touch-control-no-select" in close_button.get("class", [])
    assert overlay is not None
    assert overlay.has_attr("hidden")


def test_app_navigation_supports_escape_and_focus_return_without_inner_html():
    navigation_source = (
        Path(app_module.app.root_path) / "templates" / "_app_navigation.html"
    ).read_text()

    assert 'event.key === "Escape"' in navigation_source
    assert "openButton.focus()" in navigation_source
    assert 'setAttribute("aria-expanded", "true")' in navigation_source
    assert "innerHTML" not in navigation_source
    assert "|safe" not in navigation_source


def test_mobile_primary_buttons_use_replayable_tap_bubble_animation():
    navigation_source = (
        Path(app_module.app.root_path) / "templates" / "_app_navigation.html"
    ).read_text()
    style_source = (
        Path(app_module.app.root_path) / "static" / "style.css"
    ).read_text()

    assert (
        'const tapBubbleSelector = '
        '".app-navigation-open-button, .app-navigation-close-button, '
        '.shop-tools-fab";'
    ) in navigation_source
    assert 'document.addEventListener("pointerdown"' in navigation_source
    assert "tapBubbleButton.classList.remove(tapBubbleClass)" in (
        navigation_source
    )
    assert "void tapBubbleButton.offsetWidth" in navigation_source
    assert "tapBubbleButton.classList.add(tapBubbleClass)" in (
        navigation_source
    )
    assert 'document.addEventListener("animationend"' in navigation_source
    assert "reducedMotion.matches" in navigation_source
    assert "setInterval" not in navigation_source

    assert "@keyframes shop-tool-button-bubble" in style_source
    assert ".app-navigation-open-button::after" in style_source
    assert ".app-navigation-close-button::after" in style_source
    assert ".shop-tools-fab::after" in style_source
    assert ".is-tap-bubbling::after" in style_source
    assert "@media (prefers-reduced-motion: reduce)" in style_source
    assert ".touch-control-no-select" in style_source
    assert "-webkit-user-select: none;" in style_source
    assert "user-select: none;" in style_source
    assert "-webkit-touch-callout: none;" in style_source


def test_material_orders_has_distinct_app_and_shop_tools_navigation(
    authenticated_client,
    admin_dataset,
):
    response = authenticated_client.get("/material-orders")
    document = BeautifulSoup(response.get_data(as_text=True), "html.parser")

    assert response.status_code == 200
    assert document.select_one('nav[aria-label="主要メニュー"]') is not None
    assert document.select_one(
        'nav[aria-label="店舗メモツールのメニュー"]'
    ) is not None


@pytest.mark.parametrize(
    "route",
    ["/shop-tools/memo", "/shop-tools/memo/trash"],
)
def test_memo_pages_keep_shop_tools_current_in_both_navigations(
    authenticated_client,
    admin_dataset,
    route,
):
    response = authenticated_client.get(route)
    document = BeautifulSoup(response.get_data(as_text=True), "html.parser")

    assert response.status_code == 200
    app_link = document.select_one(
        'nav[aria-label="主要メニュー"] a[data-page="shop-tools"]'
    )
    memo_link = document.select_one(
        'nav[aria-label="店舗メモツールのメニュー"] a[data-tool="memo"]'
    )
    assert app_link.get("aria-current") == "page"
    assert "app-sidebar-link-current" in app_link.get("class", [])
    assert memo_link.get("aria-current") == "page"
    assert "shop-tools-nav-link-current" in memo_link.get("class", [])
