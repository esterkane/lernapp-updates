from datetime import date

import httpx
import pytest
from app.services import openai_billing as billing


def install_transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(billing.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def test_pagination_filters_decimal_totals_and_usage(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.headers['authorization'] == 'Bearer sk-admin-secret'
        assert request.url.params.get_list('project_ids[]') == ['proj_one']
        assert request.url.params['end_time'] == '1789084800'  # 2026-09-11 exclusive
        if request.url.path.endswith('/costs'):
            second = request.url.params.get('page') == 'two'
            return httpx.Response(200, json={'data': [{'start_time': 1, 'results': [{'amount': {'value': '-0.01' if second else '0.08', 'currency': 'usd'}, 'project_id': 'proj_one'}]}], 'has_more': not second, 'next_page': None if second else 'two'})
        return httpx.Response(200, json={'data': [{'results': [{'input_tokens': 100, 'output_tokens': 20, 'input_cached_tokens': 40, 'num_model_requests': 2}]}], 'has_more': False})
    install_transport(monkeypatch, handler)
    result = billing.fetch('sk-admin-secret', date(2026, 9, 9), date(2026, 9, 10), 'proj_one')
    assert result['total_usd'] == '0.07'
    assert result['completion_usage']['input_cached_tokens'] == 40
    assert len(calls) == 3
    assert 'sk-admin-secret' not in str(result)


def test_errors_do_not_echo_provider_body_or_secret(monkeypatch):
    install_transport(monkeypatch, lambda _: httpx.Response(403, text='sk-admin-secret'))
    with pytest.raises(billing.BillingError, match='Admin-API-Schlüssel') as caught:
        billing.fetch('sk-admin-secret', date(2026, 9, 9), date(2026, 9, 9))
    assert 'sk-admin-secret' not in str(caught.value)


def test_incomplete_costs_are_not_presented_as_zero(monkeypatch):
    install_transport(monkeypatch, lambda _: httpx.Response(200, json={'data': [{'start_time': 1, 'results': [{'amount': {'value': None, 'currency': 'usd'}}]}], 'has_more': False}))
    with pytest.raises(billing.BillingError):
        billing.fetch('key', date(2026, 9, 9), date(2026, 9, 9))


def test_validation_masks_key(client):
    result = client.post('/costs/openai/check', json={'api_key': 'sk-admin-secret', 'start': 'bad', 'end': '2026-09-09'})
    assert result.status_code == 422
    assert 'sk-admin-secret' not in result.text


def test_billing_ui_clears_submitted_key_even_on_error(monkeypatch, tmp_path):
    from lernapp_ui import api
    from streamlit.testing.v1 import AppTest
    # Minimal harness avoids coupling billing checks to local reporting calls.
    page = tmp_path / 'billing-harness.py'
    page.write_text('from lernapp_ui.billing_ui import openai_billing\nopenai_billing()\n')
    def fail(*args, **kwargs):
        raise api.ApiError('Abgelehnt')
    monkeypatch.setattr(api, '_post', fail)
    monkeypatch.setattr(api, '_get', lambda *a, **k: {'saved': False})
    app = AppTest.from_file(str(page)).run()
    app.text_input(key='billing_admin_key').input('sk-admin-secret')
    next(b for b in app.button if b.label == 'Abrechnung abrufen').click().run()
    assert not app.exception
    assert app.text_input(key='billing_admin_key').value == ''
    assert any(e.value == 'Abgelehnt' for e in app.error)


def test_verified_cached_prices_and_legacy_history():
    from datetime import UTC, datetime

    from app.core.pricing import load_pricing
    prices = load_pricing()
    current = datetime(2026, 9, 9, tzinfo=UTC)
    earlier = datetime(2026, 9, 8, tzinfo=UTC)
    assert prices.unit_price_usd('llm', 'openai/gpt-5.6-terra', 'tokens_in_cached', current) == pytest.approx(.2 / 1e6)
    assert prices.unit_price_usd('llm', 'openai/gpt-5.6-terra', 'tokens_in_cached', earlier) == pytest.approx(2 / 1e6)
    assert prices.unit_price_usd('llm', 'openai/gpt-5.6-sol', 'tokens_out', current) == pytest.approx(20 / 1e6)
    assert prices.unit_price_usd('llm', 'openai/gpt-5.6-sol', 'tokens_out', earlier) == pytest.approx(30 / 1e6)


def test_costs_remain_available_when_usage_is_unavailable(monkeypatch):
    def handler(request):
        if request.url.path.endswith('/costs'):
            return httpx.Response(200, json={'data': [], 'has_more': False})
        return httpx.Response(503)
    install_transport(monkeypatch, handler)
    result = billing.fetch('key', date(2026, 9, 9), date(2026, 9, 9))
    assert result['total_usd'] == '0'
    assert result['completion_usage'] is None
    assert result['usage_error']


def test_saved_billing_key_encrypted_reusable_and_separate(client, monkeypatch):
    from app.core import credentials
    from app.core.db import db_session
    from app.db.base import ProviderCredential
    from app.services import billing_credentials
    from sqlalchemy import select

    key = "sk-admin-test-persistent"
    headers = {"X-Lernapp-Workspace": "default"}
    prior = credentials.get_api_key("default", "openai")
    response = client.put("/costs/openai/credential", json={"api_key": key}, headers=headers)
    assert response.status_code == 200 and response.json() == {"saved": True}
    assert key not in response.text
    with db_session() as db:
        encrypted = db.scalar(select(ProviderCredential.ciphertext).where(
            ProviderCredential.learner_id == "default", ProviderCredential.provider == billing_credentials.PROVIDER))
    assert encrypted and key not in encrypted
    assert credentials._fernet().decrypt(encrypted.encode()).decode() == key
    assert client.get("/costs/openai/credential", headers=headers).json() == {"saved": True}
    calls = []
    monkeypatch.setattr(billing, "fetch", lambda key, *args: calls.append(key) or {"total_usd": "0.07"})
    payload = {"start": "2026-09-09", "end": "2026-09-09"}
    assert client.post("/costs/openai/check", json=payload, headers=headers).status_code == 200
    assert calls == [key]
    # A one-off key must not overwrite the saved one.
    assert client.post("/costs/openai/check", json={**payload, "api_key": "sk-admin-once"}, headers=headers).status_code == 200
    assert billing_credentials.get("default") == key
    assert credentials.get_api_key("default", "openai") == prior
    assert client.delete("/costs/openai/credential", headers=headers).json() == {"saved": False}
    assert billing_credentials.get("default") is None
    assert client.post("/costs/openai/check", json=payload, headers=headers).status_code == 400


def test_billing_key_workspace_isolation(client, monkeypatch):
    import uuid

    from app.core.db import db_session
    from app.db.base import Learner
    from app.services import billing_credentials

    one, two = uuid.uuid4().hex, uuid.uuid4().hex
    with db_session() as db:
        db.add_all([Learner(id=one), Learner(id=two)])
    headers = {"X-Lernapp-Workspace": one}
    try:
        assert client.put("/costs/openai/credential", json={"api_key": "sk-admin-isolation"}, headers=headers).status_code == 200
        other = {"X-Lernapp-Workspace": two}
        assert client.get("/costs/openai/credential", headers=other).json() == {"saved": False}
        monkeypatch.setattr(billing, "fetch", lambda *a: pytest.fail("Must not call provider with another workspace's key"))
        assert client.post("/costs/openai/check", json={"start": "2026-09-09", "end": "2026-09-09"}, headers=other).status_code == 400
        client.delete("/costs/openai/credential", headers=other)
        assert billing_credentials.saved(one)
    finally:
        billing_credentials.remove(one)
        with db_session() as db:
            for lid in (one, two):
                db.delete(db.get(Learner, lid))


def test_billing_ui_save_reuse_and_remove(monkeypatch, tmp_path):
    from lernapp_ui import api
    from streamlit.testing.v1 import AppTest

    stored = {}
    calls = []
    def request(method, path, **kwargs):
        assert path == "/costs/openai/credential"
        if method == "PUT":
            stored["key"] = kwargs["json"]["api_key"]
        else:
            stored.clear()
        return {"saved": bool(stored)}
    monkeypatch.setattr(api, "_request", request)
    monkeypatch.setattr(api, "_get", lambda *a, **kw: {"saved": bool(stored)})
    monkeypatch.setattr(api, "_post", lambda path, body, **kw: calls.append(body) or {})
    page = tmp_path / "saved-billing.py"
    page.write_text("from lernapp_ui.billing_ui import openai_billing\nopenai_billing()\n")
    app = AppTest.from_file(str(page)).run()
    app.text_input(key="billing_admin_key").input("sk-admin-ui-test")
    next(b for b in app.button if b.label == "Schlüssel auf diesem Gerät speichern").click().run()
    assert not app.exception and stored
    assert app.text_input(key="billing_admin_key").value == ""
    # A fresh browser session reuses the backend credential, not Streamlit state.
    app = AppTest.from_file(str(page)).run()
    next(b for b in app.button if b.label == "Abrechnung abrufen").click().run()
    assert calls[-1]["api_key"] is None
    next(b for b in app.button if b.label == "Gespeicherten Admin-Schlüssel entfernen").click().run()
    assert not app.exception and not stored
