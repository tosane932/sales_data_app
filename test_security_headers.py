import config


EXPECTED_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "X-Frame-Options": "DENY",
}

EXPECTED_CSP_DIRECTIVES = (
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "object-src 'none'",
    "form-action 'self'",
)


def test_production_cookie_policy_is_hardened():
    assert config.SESSION_COOKIE_SECURE is True
    assert config.SESSION_COOKIE_HTTPONLY is True
    assert config.SESSION_COOKIE_SAMESITE == "Lax"


def test_dynamic_response_has_minimum_security_headers_and_no_store(client):
    response = client.get("/login")

    assert response.status_code == 200

    for header_name, expected_value in EXPECTED_SECURITY_HEADERS.items():
        assert response.headers.get(header_name) == expected_value

    csp = response.headers.get("Content-Security-Policy", "")
    for directive in EXPECTED_CSP_DIRECTIVES:
        assert directive in csp

    assert "no-store" in response.headers.get("Cache-Control", "").lower()

    # HSTSは今回の実装対象外。
    assert "Strict-Transport-Security" not in response.headers


def test_static_asset_is_not_forced_to_no_store(client):
    response = client.get("/static/style.css")

    assert response.status_code == 200
    assert "no-store" not in response.headers.get(
        "Cache-Control",
        "",
    ).lower()


def _post_admin_login(client, admin_auth_config, *, base_url=None):
    from bs4 import BeautifulSoup

    request_kwargs = {}
    if base_url is not None:
        request_kwargs["base_url"] = base_url

    login_page = client.get("/login", **request_kwargs)
    assert login_page.status_code == 200

    document = BeautifulSoup(login_page.get_data(as_text=True), "html.parser")
    csrf_input = document.select_one('input[name="csrf_token"]')
    assert csrf_input is not None
    assert csrf_input.get("value")

    return client.post(
        "/login",
        data={
            "username": admin_auth_config.username,
            "password": admin_auth_config.password,
            "csrf_token": csrf_input["value"],
        },
        environ_base={"REMOTE_ADDR": "192.0.2.55"},
        follow_redirects=False,
        **request_kwargs,
    )


def test_session_cookie_has_secure_httponly_and_samesite_lax(
    flask_app,
    client,
):
    flask_app.config["SESSION_COOKIE_SECURE"] = True

    response = client.get(
        "/login",
        base_url="https://localhost",
    )

    assert response.status_code == 200

    cookie_name = flask_app.config.get("SESSION_COOKIE_NAME", "session")
    session_cookie = next(
        (
            header
            for header in response.headers.getlist("Set-Cookie")
            if header.startswith(f"{cookie_name}=")
        ),
        None,
    )

    assert session_cookie is not None
    assert "; Secure" in session_cookie
    assert "; HttpOnly" in session_cookie
    assert "SameSite=Lax" in session_cookie


def test_json_api_has_security_headers_and_no_store(
    client,
    admin_dataset,
    admin_auth_config,
):
    login_response = _post_admin_login(client, admin_auth_config)
    assert login_response.status_code == 302

    response = client.get("/api/dashboard-data")

    assert response.status_code == 200
    assert response.is_json

    for header_name, expected_value in EXPECTED_SECURITY_HEADERS.items():
        assert response.headers.get(header_name) == expected_value

    csp = response.headers.get("Content-Security-Policy", "")
    for directive in EXPECTED_CSP_DIRECTIVES:
        assert directive in csp

    assert "no-store" in response.headers.get("Cache-Control", "").lower()
