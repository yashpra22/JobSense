import importlib


def test_dashboard_requires_authentication(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password")
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "test-secret-key")
    dashboard = importlib.import_module("dashboard")
    client = dashboard.app.test_client()

    response = client.get("/api/stats")
    assert response.status_code == 401

    response = client.post("/api/control/clear_db")
    assert response.status_code == 401


def test_dashboard_is_local_only_by_default():
    import inspect
    dashboard = importlib.import_module("dashboard")
    source = inspect.getsource(dashboard)
    assert 'host="127.0.0.1"' in source
