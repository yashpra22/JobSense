import importlib
import re


def test_dashboard_requires_authentication(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password")
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "test-secret-key")
    dashboard = importlib.import_module("dashboard")
    client = dashboard.app.test_client()

    response = client.get("/api/stats")
    assert response.status_code == 401

    # Obtain a valid CSRF token first so this request reaches the authentication
    # guard rather than being rejected earlier by Flask-WTF's CSRF middleware.
    login_page = client.get("/login")
    assert login_page.status_code == 200
    match = re.search(r'name=[\'\"]csrf_token[\'\"] value=[\'\"]([^\'\"]+)', login_page.get_data(as_text=True))
    assert match, "login page must expose a CSRF token"
    csrf_token = match.group(1)

    response = client.post(
        "/api/control/clear_db",
        headers={"X-CSRFToken": csrf_token},
    )
    assert response.status_code == 401


def test_dashboard_is_local_only_by_default():
    import inspect
    dashboard = importlib.import_module("dashboard")
    source = inspect.getsource(dashboard)
    assert 'host="127.0.0.1"' in source
