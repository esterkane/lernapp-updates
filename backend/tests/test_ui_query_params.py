import httpx
from lernapp_ui import api


def test_deferred_audio_flag_reaches_backend(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"reply_text": "Hallo"})

    real_client = httpx.Client
    monkeypatch.setattr(api, "_headers", lambda: {})
    monkeypatch.setattr(api.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))
    api.session_turn_text("session", "Hallo", defer_audio=True)
    assert requests[-1].url.params["defer_audio"] == "true"
    api.session_turn_text("session", "Hallo", defer_audio=False)
    assert requests[-1].url.params["defer_audio"] == "false"
    api._get("/sessions", limit=3, kind=None)
    assert dict(requests[-1].url.params) == {"limit": "3"}
