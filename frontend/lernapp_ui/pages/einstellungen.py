"""Einstellungen – AI providers (BYOK), cost limit, speech, privacy, profile, export and deletion.

Keys are sent once to PUT /provider-credentials and popped from the widget state right away —
the UI never keeps or shows an entered key (docs/api.md "Cost tracking v2").
"""

from __future__ import annotations

from datetime import date
from typing import Any

import streamlit as st
from lernapp_ui import api
from lernapp_ui.components import PRICING_TIER_LABELS, datetime_de, money, show_error

st.title("⚙️ Einstellungen")
st.caption(
    "Schlüssel, Profil und Kostenlimit gelten für diesen Lernbereich. Spracheinstellungen und Datenschutz gelten für die gesamte Installation."
)

with st.expander("App-Updates", expanded=st.session_state.pop("show_updates", False)):
    from lernapp_ui.update_ui import render as render_updates
    render_updates()

LEVELS = ["A2", "B1", "B2", "C1", "C2"]
MAIN_PROVIDERS: list[tuple[str, str, str]] = [
    # provider id, label, placeholder
    ("gemini", "Google Gemini", "AIza…"),
    ("openai", "OpenAI", "sk-…"),
]
MORE_PROVIDERS: list[tuple[str, str, str]] = [
    ("anthropic", "Anthropic", "sk-ant-…"),
    ("mistral", "Mistral", "…"),
]
TIER_OPTIONS: list[str] = list(
    PRICING_TIER_LABELS.values()
)  # "Kostenlose Stufe" / "Kostenpflichtige API" / "Nicht sicher"
TIER_VALUES: dict[str, str] = {label: value for value, label in PRICING_TIER_LABELS.items()}
TIER_HELP = "Wenn auf deiner Gemini-Nutzungsseite 'Kostenlose Stufe' angezeigt wird, wähle 'Kostenlose Stufe'."
DELETE_WORD = "LÖSCHEN"

settings: dict[str, Any] = {}
try:
    settings = api.get_settings()
except api.ApiError as exc:
    show_error(exc, "Die Einstellungen konnten nicht geladen werden. " + api.HINT_CONNECTION)
    st.stop()

secrets = settings.get("secrets") or {}
configured = settings.get("providers_configured") or {}
speech = settings.get("speech") or {}


def _save(values: dict[str, str | None], success: str = "Gespeichert.") -> bool:
    try:
        api.put_settings(values)
        st.success(success)
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


profile_tab, costs_tab, speech_tab, providers_tab, privacy_tab = st.tabs(
    ["Profil & Ziele", "Kosten", "Stimme", "Verbindungen", "Datenschutz"]
)

with profile_tab:
    # ================================================================== profile
    st.markdown("### Dein Profil")
    learner: dict[str, Any] = {}
    try:
        learner = api.get_learner()
    except api.ApiError as exc:
        st.caption(f"Dein Profil konnte nicht geladen werden: {exc.message_de}")

    with st.form("profile_form"):
        name = st.text_input("Dein Name", value=learner.get("display_name") or "", max_chars=120)
        level_now = str(learner.get("level") or settings.get("default_level") or "C1").upper()
        level = st.selectbox("Dein Niveau", LEVELS, index=LEVELS.index(level_now) if level_now in LEVELS else 3)
        if st.form_submit_button("Profil speichern", type="primary"):
            try:
                api.patch_learner({"display_name": name.strip() or None, "level": level})
                st.success("Profil gespeichert.")
                st.rerun()
            except api.ApiError as exc:
                show_error(exc, "Das Profil konnte nicht gespeichert werden. Bitte noch einmal versuchen.")

    st.subheader("Berufliches Lernziel")
    try:
        goal_profile = api.get_learner()
        with st.form("professional_goal"):
            goal = st.text_area(
                "Was möchtest du im Beruf sicher auf Deutsch erledigen?",
                value=goal_profile.get("learning_goal") or "",
                max_chars=600,
                placeholder="Zum Beispiel: Als Collections Manager Zahlungsvereinbarungen besprechen und offene Posten erklären.",
            )
            st.caption(
                "Wird im Lernbereich gespeichert und als Kontext an den Tutor gesendet. Keine echten Kunden- oder Kontodaten eintragen. Leeren und speichern entfernt die eigene Angabe."
            )
            if st.form_submit_button("Lernziel speichern"):
                api.patch_learner({"learning_goal": goal})
                st.success("Lernziel gespeichert.")
    except api.ApiError as exc:
        show_error(exc, api.HINT_CONNECTION)

with costs_tab:
    if st.button("Anbieterabrechnung prüfen"):
        st.switch_page("pages/kosten.py")
    # ================================================================== cost limit
    st.markdown("### Kostenlimit")
    usage_settings: dict[str, Any] = {}
    try:
        usage_settings = api.get_usage_settings()
    except api.ApiError as exc:
        st.caption(f"Das Kostenlimit konnte nicht geladen werden: {exc.message_de}")

    current_budget = usage_settings.get("monthly_budget_eur")
    budget_value = st.number_input(
        "Monatslimit in Euro (0 = kein Limit)",
        min_value=0.0,
        value=float(current_budget) if current_budget is not None else 0.0,
        step=1.0,
        format="%.2f",
        key="usage_budget_eur",
        help="Bezieht sich auf die geschätzten AI-Kosten des laufenden Monats.",
    )
    stop_on_limit = st.toggle(
        "AI-Nutzung beim Erreichen des Limits stoppen",
        value=bool(usage_settings.get("stop_on_limit", False)),
        key="usage_stop_on_limit",
    )
    st.caption(
        "Dieses optionale App-Limit basiert auf internen Schätzungen und kann von der Anbieterabrechnung abweichen. "
        "Mit dem Schalter stoppt die App neue AI-Anfragen beim geschätzten Limit. Verbindliche Ausgabenlimits bitte beim Anbieter verwalten."
    )
    if st.button("Kostenlimit speichern", type="primary", key="usage_budget_save"):
        try:
            saved = api.put_usage_settings(
                float(budget_value) if budget_value and budget_value > 0 else None, stop_on_limit
            )
            limit_text = (
                money(saved.get("monthly_budget_eur")) if saved.get("monthly_budget_eur") is not None else "kein Limit"
            )
            st.success(f"Kostenlimit gespeichert: {limit_text}.")
        except api.ApiError as exc:
            show_error(exc, "Das Kostenlimit konnte nicht gespeichert werden. Bitte noch einmal versuchen.")

    st.subheader("Sparmodus")
    try:
        economy_profile = api.get_learner()
        with st.form("economy_settings"):
            economy_enabled = st.checkbox(
                "Zusätzliche KI-Aussprachetipps im Gespräch ausschalten",
                value=bool(economy_profile.get("economy_mode", False)),
                help="Spracherkennung und lokale Audioanalyse bleiben aktiv. Antworten und Vorlesen verwenden weiterhin die eingestellten Anbieter.",
            )
            if st.form_submit_button("Sparmodus speichern"):
                api.patch_learner({"economy_mode": economy_enabled})
                st.success("Sparmodus gespeichert.")
    except api.ApiError as exc:
        show_error(exc, api.HINT_CONNECTION)

with speech_tab:
    from lernapp_ui.speech_ui import render as render_speech
    render_speech(settings)

with providers_tab:
    # ================================================================== AI providers (BYOK)
    st.markdown("### KI-Anbieter")
    st.caption(
        "Du nutzt deine eigenen Zugänge zu den AI-Anbietern. Die Schlüssel werden verschlüsselt auf diesem Gerät "
        "gespeichert und nie angezeigt – nur die letzten Zeichen."
    )

    credentials: dict[str, dict[str, Any]] = {}
    try:
        for cred in api.provider_credentials():
            credentials[str(cred.get("provider") or "")] = cred
    except api.ApiError as exc:
        show_error(exc, "Die AI-Anbieter konnten nicht geladen werden. " + api.HINT_CONNECTION)


    def _flash(provider: str, kind: str, text: str) -> None:
        st.session_state[f"cred_msg_{provider}"] = (kind, text)


    def _show_flash(provider: str) -> None:
        flash = st.session_state.pop(f"cred_msg_{provider}", None)
        if not flash:
            return
        kind, text = flash
        {"success": st.success, "error": st.error, "info": st.info}.get(kind, st.info)(text)


    def _submit_key(provider: str, label: str) -> None:
        """Button callback: pop the entered key from the widget state, send it once, never keep it."""
        entered = str(st.session_state.pop(f"cred_newkey_{provider}", "") or "").strip()
        st.session_state[f"cred_reveal_{provider}"] = False
        if not entered:
            _flash(provider, "info", "Kein Schlüssel eingegeben.")
            return
        try:
            api.put_provider_credential(provider, api_key=entered)
        except api.ApiError as exc:
            _flash(provider, "error", f"Der Schlüssel konnte nicht gespeichert werden: {exc.message_de}")
            return
        finally:
            del entered
        _flash(provider, "success", f"Schlüssel für {label} gespeichert.")


    def _save_tier(provider: str) -> None:
        """Radio callback: PUT only the pricing tier."""
        choice = st.session_state.get(f"cred_tier_{provider}")
        tier = TIER_VALUES.get(str(choice), "unknown")
        try:
            api.put_provider_credential(provider, pricing_tier=tier)
        except api.ApiError as exc:
            _flash(provider, "error", f"Der Tarif konnte nicht gespeichert werden: {exc.message_de}")
            return
        _flash(provider, "success", f"Tarif gespeichert: {choice}.")


    def _test_credential(provider: str, label: str) -> None:
        with st.spinner(f"Verbindung zu {label} wird geprüft…"):
            try:
                res = api.test_provider_credential(provider)
            except api.ApiError as exc:
                show_error(exc, "Die Verbindung konnte nicht geprüft werden.")
                return
        if res.get("ok"):
            st.success(res.get("message_de") or f"{label} verbunden")
        else:
            st.error(res.get("message_de") or f"{label} antwortet nicht. Prüfe bitte den Schlüssel.")


    def _provider_card(provider: str, label: str, placeholder: str) -> None:
        cred = credentials.get(provider) or {}
        label = str(cred.get("label_de") or label)
        connected = bool(cred.get("connected"))
        source = str(cred.get("source") or "none")
        with st.container(border=True):
            head_left, head_right = st.columns([2, 1])
            head_left.markdown(f"**{label}**")
            if connected and source == "env":
                head_right.markdown(":green[● Verbunden (aus Konfigurationsdatei)]")
            elif connected:
                head_right.markdown(":green[● Verbunden]")
            else:
                head_right.markdown(":gray[○ Nicht verbunden]")
            if connected and cred.get("masked"):
                st.caption(f"API-Schlüssel {cred['masked']}")
            _show_flash(provider)

            if provider == "gemini":
                current_tier = str(cred.get("pricing_tier") or "unknown")
                tier_label = PRICING_TIER_LABELS.get(current_tier, PRICING_TIER_LABELS["unknown"])
                tier_key = f"cred_tier_{provider}"
                if tier_key not in st.session_state:
                    st.session_state[tier_key] = tier_label
                st.radio(
                    "Welchen Gemini-Tarif verwendest du?",
                    TIER_OPTIONS,
                    key=tier_key,
                    horizontal=True,
                    help=TIER_HELP,
                    on_change=_save_tier,
                    args=(provider,),
                )

            b1, b2, b3 = st.columns(3)
            if b1.button("Verbindung testen", key=f"cred_test_{provider}", disabled=not connected, width="stretch"):
                _test_credential(provider, label)
            if b2.button(
                "Schlüssel ändern" if connected else "Schlüssel eintragen", key=f"cred_chg_{provider}", width="stretch"
            ):
                st.session_state[f"cred_reveal_{provider}"] = not st.session_state.get(f"cred_reveal_{provider}", False)
            can_remove = connected and source == "encrypted"
            if b3.button("Verbindung entfernen", key=f"cred_rm_{provider}", disabled=not can_remove, width="stretch"):
                st.session_state[f"cred_confirm_rm_{provider}"] = True

            if st.session_state.get(f"cred_reveal_{provider}"):
                st.text_input(
                    f"Neuer Schlüssel für {label}",
                    type="password",
                    placeholder=placeholder,
                    key=f"cred_newkey_{provider}",
                    help="Der Schlüssel wird einmal gesendet und danach nirgends angezeigt.",
                )
                st.button(
                    "Speichern",
                    key=f"cred_save_{provider}",
                    type="primary",
                    on_click=_submit_key,
                    args=(provider, label),
                )

            if st.session_state.get(f"cred_confirm_rm_{provider}"):
                sure = st.checkbox(f"Ja, die Verbindung zu {label} wirklich entfernen.", key=f"cred_sure_{provider}")
                r1, r2 = st.columns(2)
                if r1.button("Jetzt entfernen", key=f"cred_rm_go_{provider}", disabled=not sure, type="primary"):
                    try:
                        api.delete_provider_credential(provider)
                        st.session_state.pop(f"cred_confirm_rm_{provider}", None)
                        _flash(provider, "success", f"Verbindung zu {label} entfernt.")
                        st.rerun()
                    except api.ApiError as exc:
                        show_error(exc, "Die Verbindung konnte nicht entfernt werden.")
                if r2.button("Abbrechen", key=f"cred_rm_cancel_{provider}"):
                    st.session_state.pop(f"cred_confirm_rm_{provider}", None)
                    st.rerun()

            if cred.get("last_test_at"):
                verdict = "erfolgreich" if cred.get("last_test_ok") else "fehlgeschlagen"
                note = f" – {cred['last_test_message']}" if cred.get("last_test_message") else ""
                st.caption(f"zuletzt geprüft: {datetime_de(cred['last_test_at'])} ({verdict}{note})")


    for _provider, _label, _placeholder in MAIN_PROVIDERS:
        _provider_card(_provider, _label, _placeholder)

    validator = settings.get("validator") or {}
    validator_choices = validator.get("choices") or []
    if validator_choices:
        st.subheader("Zweitmeinung")
        st.caption(
            "Dieses Modell prüft Bewertungen und erzeugte Aufgaben unabhängig. Die Auswahl gilt für diesen Lernbereich; bei Ausfällen kann ein verbundenes Ersatzmodell übernehmen."
        )
        options = [None, *validator_choices]
        selected = validator.get("selected")
        with st.form("validator_choice"):
            choice = st.selectbox(
                "Modell für die Zweitmeinung",
                options,
                index=options.index(selected) if selected in options else 0,
                format_func=lambda value: (
                    "Automatisch (bisherige Einstellung)" if value is None else value.split("/", 1)[-1]
                ),
                disabled=bool(settings.get("eu_strict_mode")),
            )
            if st.form_submit_button("Modellauswahl speichern"):
                try:
                    api.patch_learner({"validator_model": choice})
                except api.ApiError as exc:
                    show_error(exc)
                else:
                    st.success("Modellauswahl gespeichert.")
                    st.rerun()
        last_used = validator.get("last_used")
        if last_used:
            st.caption(f"Letzte erfolgreiche Zweitmeinung: {last_used['model']} · {last_used['at']}")
        if not validator.get("available"):
            st.info(validator.get("hint_de") or "Bitte zuerst den Anbieter verbinden.")

    with st.expander("Weitere Anbieter"):
        st.caption("Anthropic als Alternative für die Zweitmeinung, Mistral für den EU-Modus.")
        for _provider, _label, _placeholder in MORE_PROVIDERS:
            _provider_card(_provider, _label, _placeholder)

with privacy_tab:
    # ================================================================== privacy
    st.markdown("### Datenschutz")
    keep_audio = st.toggle(
        "Aufnahmen behalten",
        value=bool(settings.get("keep_audio", False)),
        help="Standard: aus. Deine Sprachaufnahmen werden sofort nach der Auswertung gelöscht.",
        key="settings_keep_audio",
    )
    eu_mode = st.toggle(
        "EU-Modus",
        value=bool(settings.get("eu_strict_mode", False)),
        help="Nur Anbieter mit Servern in der EU verwenden – dafür kann ein zusätzlicher Schlüssel nötig sein.",
        key="settings_eu_mode",
    )
    st.caption(
        "Aufnahmen aus (empfohlen): direkt nach der Auswertung gelöscht. "
        "EU-Modus an: wo möglich nur europäische Anbieter – die Qualität kann sich leicht unterscheiden."
    )
    if st.button("Datenschutz speichern", type="primary") and _save(
        {"KEEP_AUDIO": "true" if keep_audio else "false", "EU_STRICT_MODE": "true" if eu_mode else "false"}
    ):
        st.rerun()

    # ================================================================== export
    st.markdown("### Meine Daten herunterladen")
    st.caption(
        "Alle deine Ergebnisse, Dokumente und Vokabeln als ZIP-Datei (JSON und CSV) – du kannst sie jederzeit mitnehmen."
    )
    if st.button("Export vorbereiten"):
        with st.spinner("Deine Daten werden zusammengestellt…"):
            try:
                st.session_state["export_zip"] = api.export_learner()
            except api.ApiError as exc:
                show_error(exc, "Der Export konnte nicht erstellt werden. Bitte noch einmal versuchen.")
    if st.session_state.get("export_zip"):
        st.download_button(
            "ZIP-Datei herunterladen",
            data=st.session_state["export_zip"],
            file_name=f"lernapp-export-{date.today().isoformat()}.zip",
            mime="application/zip",
            type="primary",
        )

    with st.expander("Alle Daten löschen"):
        # ================================================================== danger zone
        st.markdown("### Alle meine Daten löschen")
        st.warning(
            "Das löscht dein Profil, alle Ergebnisse, Dokumente und Vokabeln unwiderruflich. "
            "Lade vorher deine Daten herunter, wenn du sie behalten möchtest."
        )
        confirm = st.text_input(f"Zur Bestätigung „{DELETE_WORD}“ eingeben", key="delete_confirm")
        if st.button("Alle meine Daten löschen", type="primary", disabled=confirm.strip() != DELETE_WORD):
            try:
                api.delete_learner()
            except api.ApiError as exc:
                show_error(exc, "Die Daten konnten nicht gelöscht werden. Bitte noch einmal versuchen.")
                st.stop()
            for key in list(st.session_state.keys()):
                if not str(key).startswith("_"):
                    st.session_state.pop(key, None)
            st.success("Alle Daten wurden gelöscht. Die App beginnt beim nächsten Start von vorn.")
