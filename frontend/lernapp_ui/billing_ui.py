"""Billing with an optional encrypted device-local admin key; never prefill secrets."""

from datetime import date, timedelta

import streamlit as st

from lernapp_ui import api


def _check() -> None:
    key = st.session_state.pop("billing_admin_key", "")
    st.session_state.pop("openai_billing_result", None)
    st.session_state.pop("openai_billing_error", None)
    try:
        st.session_state["openai_billing_result"] = api._post(
            "/costs/openai/check",
            {
                "api_key": key.strip() or None,
                "start": st.session_state["billing_start"].isoformat(),
                "end": st.session_state["billing_end"].isoformat(),
                "project_id": st.session_state.get("billing_project", "").strip() or None,
                "organization_id": st.session_state.get("billing_org", "").strip() or None,
                "api_key_id": st.session_state.get("billing_key_id", "").strip() or None,
            },
            timeout=65,
        )
    except api.ApiError as exc:
        st.session_state["openai_billing_error"] = exc.message_de


def _save_key() -> None:
    key = st.session_state.pop("billing_admin_key", "")
    st.session_state.pop("openai_billing_result", None)
    st.session_state.pop("openai_billing_error", None)
    try:
        api._request("PUT", "/costs/openai/credential", json={"api_key": key})
        st.session_state["billing_key_notice"] = "Admin-Schlüssel auf diesem Gerät gespeichert."
    except api.ApiError as exc:
        st.session_state["openai_billing_error"] = exc.message_de


def _remove_key() -> None:
    for name in ("billing_admin_key", "openai_billing_result", "openai_billing_error", "billing_key_notice"):
        st.session_state.pop(name, None)
    try:
        api._request("DELETE", "/costs/openai/credential")
        st.session_state["billing_key_notice"] = "Gespeicherter Admin-Schlüssel entfernt."
    except api.ApiError as exc:
        st.session_state["openai_billing_error"] = exc.message_de


def openai_billing() -> None:
    st.subheader("OpenAI-Abrechnung prüfen")
    st.link_button("OpenAI-Dashboard öffnen", "https://platform.openai.com/usage")
    st.caption(
        "Die Anmeldung erfolgt direkt bei OpenAI. Für den Abruf in Lernapp ist ein Admin-API-Schlüssel erforderlich; eine Google-Anmeldung gibt Lernapp keinen Abrechnungszugriff."
    )
    with st.expander("Anbieterbeträge direkt abrufen"):
        st.markdown(
            "[Admin-Schlüssel bei OpenAI verwalten](https://platform.openai.com/settings/organization/admin-keys)"
        )
        st.caption(
            "Du kannst den Admin-Schlüssel verschlüsselt für diesen Lernbereich auf diesem Gerät speichern. Er bleibt bei App-Updates erhalten und wird nicht in Lernpakete übernommen. Zeitraum und Projekt müssen mit dem Dashboard übereinstimmen. Beträge bleiben in US-Dollar."
        )
        try:
            has_saved_key = bool(api._get("/costs/openai/credential").get("saved"))
        except api.ApiError:
            has_saved_key = False
            st.caption(
                "Der Speicherstatus konnte nicht geladen werden. Du kannst den Schlüssel für einen einzelnen Abruf eingeben."
            )
        if has_saved_key:
            st.success("Admin-Schlüssel gespeichert. Für die nächste Abfrage ist keine erneute Eingabe nötig.")
            st.button("Gespeicherten Admin-Schlüssel entfernen", on_click=_remove_key)
        if notice := st.session_state.pop("billing_key_notice", None):
            st.success(notice)
        with st.form("openai_billing_check"):
            st.text_input(
                "OpenAI-Admin-API-Schlüssel",
                type="password",
                key="billing_admin_key",
                help="Leer lassen, um den gespeicherten Schlüssel zu verwenden. Eine neue Eingabe wird nur mit „Schlüssel auf diesem Gerät speichern“ gespeichert oder ersetzt.",
            )
            first, last = st.columns(2)
            first.date_input("Von (UTC)", date.today() - timedelta(days=6), key="billing_start")
            last.date_input("Bis einschließlich (UTC)", date.today(), key="billing_end")
            st.text_input("Projekt-ID (leer = alle Projekte)", key="billing_project", placeholder="proj_…")
            st.text_input("Organisations-ID (optional)", key="billing_org", placeholder="org-…")
            st.text_input("API-Schlüssel-ID filtern (optional; kein geheimer Schlüssel)", key="billing_key_id")
            st.form_submit_button("Abrechnung abrufen", on_click=_check)
            st.form_submit_button("Schlüssel auf diesem Gerät speichern", on_click=_save_key)
        if st.session_state.get("openai_billing_error"):
            st.error(st.session_state["openai_billing_error"])
        result = st.session_state.get("openai_billing_result")
        if result:
            st.metric("Von OpenAI gemeldeter Betrag (USD)", f"${float(result['total_usd']):.4f}")
            period = result["period"]
            st.caption(
                f"{period['from']} bis {period['to_inclusive']} (UTC) · Projekt: {result.get('project_id') or 'alle'} · Abruf: {result['fetched_at']}"
            )
            st.caption(result["scope_note"])
            usage = result.get("completion_usage")
            if usage:
                st.write(
                    f"Sprachmodell-Anfragen: {usage['num_model_requests']} · Eingabetokens: {usage['input_tokens']} (davon gecacht: {usage['input_cached_tokens']}) · Ausgabetokens: {usage['output_tokens']}"
                )
                st.caption(
                    "Diese Nutzungszahlen betreffen Sprachmodell-Anfragen. Der Betrag oben umfasst alle Kostenarten im gewählten Filter."
                )
            if result.get("usage_error"):
                st.info(result["usage_error"])
            if result.get("cost_rows"):
                st.dataframe(result["cost_rows"], hide_index=True)
    st.markdown(
        "[Aktuelle OpenAI-Preisliste](https://developers.openai.com/api/docs/pricing) · [Dokumentation zur Kostenabfrage](https://developers.openai.com/api/reference/python/resources/admin/subresources/organization/subresources/usage/methods/costs)"
    )
