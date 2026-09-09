"""Einrichtung – four-step setup wizard for a non-technical learner."""

from __future__ import annotations

from typing import Any

import streamlit as st
from lernapp_ui import api
from lernapp_ui.components import PRICING_TIER_LABELS, show_error

st.title("🧭 Einrichtung")

STEPS = ["OpenAI-Schlüssel", "Zweitmeinung", "Sprache", "Datenschutz"]
TIER_OPTIONS: list[str] = list(PRICING_TIER_LABELS.values())
TIER_VALUES: dict[str, str] = {label: value for value, label in PRICING_TIER_LABELS.items()}
TIER_HELP = "Wenn auf deiner Gemini-Nutzungsseite 'Kostenlose Stufe' angezeigt wird, wähle 'Kostenlose Stufe'."

if "setup_step" not in st.session_state:
    st.session_state["setup_step"] = 0

settings: dict[str, Any] = {}
try:
    settings = api.get_settings()
except api.ApiError as exc:
    show_error(exc, "Die aktuellen Einstellungen konnten nicht geladen werden – du kannst trotzdem fortfahren.")

step = int(st.session_state["setup_step"])
step = max(0, min(step, len(STEPS)))

if st.session_state.get("_first_run"):
    st.info("Willkommen! In vier kurzen Schritten ist die App startklar.", icon="👋")

if step < len(STEPS):
    st.progress((step + 1) / len(STEPS), text=f"Schritt {step + 1} von {len(STEPS)}: {STEPS[step]}")


def _go(delta: int) -> None:
    st.session_state["setup_step"] = max(0, min(int(st.session_state["setup_step"]) + delta, len(STEPS)))
    st.rerun()


def _save(values: dict[str, str | None]) -> bool:
    try:
        api.put_settings(values)
        return True
    except api.ApiError as exc:
        show_error(exc, "Die Einstellung konnte nicht gespeichert werden. Bitte noch einmal versuchen.")
        return False


def _test(provider: str, label: str) -> None:
    with st.spinner(f"Verbindung zu {label} wird geprüft…"):
        try:
            res = api.test_provider(provider)
        except api.ApiError as exc:
            show_error(exc, "Die Verbindung konnte nicht geprüft werden.")
            return
    if res.get("ok"):
        st.success(f"✅ {label} antwortet. Alles bereit.")
    else:
        st.error(f"❌ {label} antwortet nicht: {res.get('detail') or 'unbekannter Grund'}. Prüfe bitte den Schlüssel.")


def _flash(provider: str, kind: str, text: str) -> None:
    st.session_state.setdefault("setup_msgs", []).append((provider, kind, text))


def _show_flash(provider: str) -> None:
    msgs: list[tuple[str, str, str]] = st.session_state.get("setup_msgs") or []
    keep: list[tuple[str, str, str]] = []
    for prov, kind, text in msgs:
        if prov != provider:
            keep.append((prov, kind, text))
            continue
        {"success": st.success, "error": st.error, "info": st.info}.get(kind, st.info)(text)
    st.session_state["setup_msgs"] = keep


def _test_credential(provider: str, label: str) -> None:
    with st.spinner(f"Verbindung zu {label} wird geprüft…"):
        try:
            res = api.test_provider_credential(provider)
        except api.ApiError as exc:
            _flash(provider, "error", f"Die Verbindung konnte nicht geprüft werden: {exc.message_de}")
            return
    if res.get("ok"):
        _flash(provider, "success", f"✅ {res.get('message_de') or f'{label} verbunden'}. Alles bereit.")
    else:
        _flash(
            provider, "error", f"❌ {res.get('message_de') or f'{label} antwortet nicht.'} Prüfe bitte den Schlüssel."
        )


def _save_and_test(provider: str, label: str, widget_key: str, tier_key: str | None = None) -> None:
    """Button callback: pop the key from the widget state, send it once (plus the tier), then test."""
    entered = str(st.session_state.pop(widget_key, "") or "").strip()
    tier = TIER_VALUES.get(str(st.session_state.get(tier_key, ""))) if tier_key else None
    if not entered:
        _flash(provider, "info", "Bitte zuerst einen Schlüssel eingeben.")
        return
    try:
        api.put_provider_credential(provider, api_key=entered, pricing_tier=tier)
    except api.ApiError as exc:
        _flash(provider, "error", f"Die Einstellung konnte nicht gespeichert werden: {exc.message_de}")
        return
    finally:
        del entered
    st.session_state[f"setup_{provider}_saved"] = True
    st.session_state.pop("_setup_credentials", None)  # reload the masked view on the next run
    _test_credential(provider, label)


def _save_tier(provider: str, tier_key: str) -> None:
    tier = TIER_VALUES.get(str(st.session_state.get(tier_key, "")), "unknown")
    try:
        api.put_provider_credential(provider, pricing_tier=tier)
    except api.ApiError as exc:
        _flash(provider, "error", f"Der Tarif konnte nicht gespeichert werden: {exc.message_de}")


def _credential(provider: str) -> dict[str, Any]:
    if "_setup_credentials" not in st.session_state:
        try:
            st.session_state["_setup_credentials"] = {str(c.get("provider")): c for c in api.provider_credentials()}
        except api.ApiError:
            st.session_state["_setup_credentials"] = {}
    creds: dict[str, dict[str, Any]] = st.session_state["_setup_credentials"]
    return creds.get(provider) or {}


secrets = settings.get("secrets") or {}
configured = settings.get("providers_configured") or {}
speech = settings.get("speech") or {}

# ------------------------------------------------------------------ step 1
if step == 0:
    st.subheader("Schritt 1 · OpenAI-Schlüssel (nötig)")
    st.write(
        "Die App nutzt ein Sprachmodell von OpenAI für Gespräche, Aufgaben und Bewertungen. "
        "Dafür brauchst du einen persönlichen Schlüssel von OpenAI. Er wird nur auf diesem Gerät gespeichert."
    )
    cred_openai = _credential("openai")
    openai_connected = bool(cred_openai.get("connected") or configured.get("openai"))
    if cred_openai.get("masked"):
        st.success(f"Ein Schlüssel ist bereits gespeichert ({cred_openai['masked']}).")
    elif secrets.get("OPENAI_API_KEY"):
        st.success(f"Ein Schlüssel ist bereits gespeichert ({secrets['OPENAI_API_KEY']}).")
    _show_flash("openai")
    key = st.text_input("OpenAI-Schlüssel", type="password", placeholder="sk-…", key="setup_openai_key")
    c1, c2 = st.columns(2)
    c1.button(
        "Speichern und prüfen",
        type="primary",
        disabled=not key,
        on_click=_save_and_test,
        args=("openai", "OpenAI", "setup_openai_key"),
    )
    if c2.button("Nur prüfen", disabled=not (openai_connected or st.session_state.get("setup_openai_saved"))):
        _test_credential("openai", "OpenAI")
        st.rerun()
    st.divider()
    can_continue = bool(openai_connected or st.session_state.get("setup_openai_saved"))
    if st.button("Weiter →", disabled=not can_continue):
        _go(1)
    if not can_continue:
        st.caption("Bitte zuerst einen Schlüssel speichern.")

# ------------------------------------------------------------------ step 2
elif step == 1:
    st.subheader("Schritt 2 · Zweitmeinung: Gemini oder Anthropic (einer reicht)")
    st.write(
        "Ein **zweites Sprachmodell eines anderen Anbieters** prüft erzeugte Aufgaben und gibt bei "
        "Fortschrittsmessungen eine unabhängige zweite Meinung. Ohne diesen Schlüssel können keine "
        "Übungsaufgaben erstellt werden. Google Gemini und Anthropic kosten dafür etwa gleich viel – "
        "trage den ein, den du hast."
    )
    cred_gemini = _credential("gemini")
    cred_anthropic = _credential("anthropic")
    gemini_connected = bool(cred_gemini.get("connected") or configured.get("gemini"))
    anthropic_connected = bool(cred_anthropic.get("connected") or configured.get("anthropic"))
    if gemini_connected or anthropic_connected:
        st.success("Ein Anbieter für die Zweitmeinung ist eingerichtet.")
    tab_g, tab_a = st.tabs(["Gemini (Google)", "Anthropic"])
    with tab_g:
        masked_g = cred_gemini.get("masked") or secrets.get("GEMINI_API_KEY")
        if masked_g:
            st.caption(f"Gespeichert: {masked_g}")
        _show_flash("gemini")
        key_g = st.text_input("Gemini-Schlüssel", type="password", placeholder="AIza…", key="setup_gemini_key")
        tier_now = str(cred_gemini.get("pricing_tier") or "unknown")
        if "setup_gemini_tier" not in st.session_state:
            st.session_state["setup_gemini_tier"] = PRICING_TIER_LABELS.get(tier_now, PRICING_TIER_LABELS["unknown"])
        st.radio(
            "Welchen Gemini-Tarif verwendest du?",
            TIER_OPTIONS,
            key="setup_gemini_tier",
            horizontal=True,
            help=TIER_HELP,
            on_change=_save_tier if gemini_connected else None,
            args=("gemini", "setup_gemini_tier") if gemini_connected else None,
        )
        g1, g2 = st.columns(2)
        g1.button(
            "Speichern und prüfen",
            type="primary",
            disabled=not key_g,
            key="g_save",
            on_click=_save_and_test,
            args=("gemini", "Gemini", "setup_gemini_key", "setup_gemini_tier"),
        )
        if g2.button("Nur prüfen", disabled=not gemini_connected, key="g_test"):
            _test_credential("gemini", "Gemini")
            st.rerun()
    with tab_a:
        masked_a = cred_anthropic.get("masked") or secrets.get("ANTHROPIC_API_KEY")
        if masked_a:
            st.caption(f"Gespeichert: {masked_a}")
        _show_flash("anthropic")
        key = st.text_input("Anthropic-Schlüssel", type="password", placeholder="sk-ant-…", key="setup_anthropic_key")
        c1, c2 = st.columns(2)
        c1.button(
            "Speichern und prüfen",
            type="primary",
            disabled=not key,
            key="a_save",
            on_click=_save_and_test,
            args=("anthropic", "Anthropic", "setup_anthropic_key"),
        )
        if c2.button("Nur prüfen", disabled=not anthropic_connected, key="a_test"):
            _test_credential("anthropic", "Anthropic")
            st.rerun()
    st.divider()
    b1, b2 = st.columns(2)
    if b1.button("← Zurück"):
        _go(-1)
    if b2.button("Weiter →", type="primary"):
        _go(1)

# ------------------------------------------------------------------ step 3
elif step == 2:
    st.subheader("Schritt 3 · Sprache")
    from lernapp_ui.speech_ui import render as render_speech
    render_speech(settings)
    st.divider()
    b1, b2 = st.columns(2)
    if b1.button("← Zurück"):
        _go(-1)
    if b2.button("Weiter →", type="primary"):
        _go(1)

# ------------------------------------------------------------------ step 4
elif step == 3:
    st.subheader("Schritt 4 · Datenschutz")
    keep_audio = st.toggle(
        "Aufnahmen behalten",
        value=bool(settings.get("keep_audio", False)),
        help="Standard: aus. Deine Sprachaufnahmen werden sofort nach der Auswertung gelöscht.",
        key="setup_keep_audio",
    )
    st.caption(
        "Aus (empfohlen): Aufnahmen werden direkt nach der Auswertung gelöscht. "
        "An: Aufnahmen werden verschlüsselt gespeichert, damit du sie später anhören kannst."
    )
    eu_mode = st.toggle(
        "EU-Modus",
        value=bool(settings.get("eu_strict_mode", False)),
        help="Nur Anbieter mit Servern in der EU verwenden – dafür kann ein zusätzlicher Schlüssel nötig sein.",
        key="setup_eu_mode",
    )
    st.caption("Der EU-Modus nutzt, wo möglich, nur europäische Anbieter. Die Qualität kann sich leicht unterscheiden.")
    if st.button("Datenschutz speichern") and _save(
        {"KEEP_AUDIO": "true" if keep_audio else "false", "EU_STRICT_MODE": "true" if eu_mode else "false"}
    ):
        st.success("Gespeichert.")
    st.divider()
    b1, b2 = st.columns(2)
    if b1.button("← Zurück"):
        _go(-1)
    if b2.button("Einrichtung abschließen ✅", type="primary"):
        _save({"KEEP_AUDIO": "true" if keep_audio else "false", "EU_STRICT_MODE": "true" if eu_mode else "false"})
        st.session_state["setup_step"] = 0
        st.session_state["_first_run"] = False
        st.switch_page("pages/start.py")
