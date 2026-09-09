"""Two clear speech tasks, explicit downloads and tests, automatic setup status."""

from __future__ import annotations

from typing import Any

import streamlit as st

from lernapp_ui import api
from lernapp_ui.components import show_error

MODELS = {
    "small": "Schnell – für die meisten Laptops (empfohlen)",
    "medium": "Gründlicher – braucht mehr Zeit und Speicher",
    "large-v3-turbo": "Für leistungsstarke Laptops",
}
VOICES = {
    "piper": "Auf diesem Laptop – deutsche Texte",
    "openai_mini_tts": "Online – Deutsch und Englisch (OpenAI)",
    "google_wavenet": "Online – Google",
    "voxtral_tts": "Online – Mistral",
    "none": "Nicht vorlesen",
}


def _call(path: str, **kwargs: Any) -> Any:
    return api._request("POST", "/settings/speech/" + path, **kwargs)


def _action(path: str, body: dict[str, Any]) -> bool:
    try:
        _call(path, json=body)
    except api.ApiError as exc:
        show_error(exc)
        return False
    return True


@st.fragment(run_every=2)
def _installation_status() -> None:
    try:
        local = api.local_speech_status()
    except api.ApiError:
        return
    recognition = local.get("recognition", {}).get("job", {})
    voice = local.get("voice", {})
    busy = recognition.get("state") == "installing" or voice.get("state") == "installing"
    for item in (recognition, voice):
        if item.get("state") == "installing":
            st.info(item["message"])
    was_busy = st.session_state.get("speech_setup_was_busy", False)
    st.session_state["speech_setup_was_busy"] = busy
    if was_busy and not busy:
        st.rerun(scope="app")


def render(settings: dict[str, Any]) -> None:
    st.subheader("Sprechen und zuhören")
    st.caption(
        "Einmal einrichten, kurz ausprobieren, dann lernen. Diese Auswahl gilt für alle Lernbereiche auf diesem Laptop."
    )
    try:
        local = api.local_speech_status()
    except api.ApiError as exc:
        show_error(exc, "Die lokale Einrichtung konnte nicht geprüft werden.")
        return
    speech = settings.get("speech") or {}
    recognition = local.get("recognition") or {}
    job = recognition.get("job") or {}
    voice = local.get("voice") or {}
    if job.get("state") == "installing" or voice.get("state") == "installing":
        st.session_state["speech_setup_was_busy"] = True
        _installation_status()

    with st.container(border=True):
        st.markdown("### 1. Dich verstehen")
        st.write(
            "Die App wandelt deine Aufnahme in Text um. Mit der lokalen Spracherkennung bleibt dieser Schritt auf deinem Laptop."
        )
        current = str(speech.get("stt_model") or "small")
        current_backend = speech.get("stt_backend", "")
        st.caption(
            "Aktuell: "
            + (
                MODELS.get(current, current) + " · auf diesem Laptop"
                if current_backend in ("faster_whisper", "faster-whisper")
                else "Online-Spracherkennung"
            )
        )
        model = st.selectbox(
            "Lokale Spracherkennung",
            list(MODELS),
            index=list(MODELS).index(current) if current in MODELS else 0,
            format_func=lambda value: MODELS[str(value)],
            key="speech_recognition_choice",
        )
        info = recognition.get("models", {}).get(model, {})
        installed = bool(info.get("installed"))
        installing = job.get("state") == "installing"
        if installed:
            st.caption("✓ Auf diesem Laptop installiert")
        else:
            st.caption(
                f"Einmaliger Download: {info.get('download', 'mehrere hundert MB')}. Danach ohne Internet für die Spracherkennung nutzbar."
            )
        if job.get("state") == "error" and job.get("model") == model:
            st.error(job.get("message", "Einrichtung fehlgeschlagen."))
        needs_setup = not installed or (job.get("state") == "error" and job.get("model") == model)
        if (
            needs_setup
            and st.button("Spracherkennung herunterladen und einrichten", disabled=installing, key="speech_stt_install")
            and _action("recognition/install", {"model": model})
        ):
            st.session_state["speech_setup_was_busy"] = True
            st.rerun()
        active = current_backend in ("faster_whisper", "faster-whisper") and current == model
        if (
            installed
            and not active
            and st.button("Diese Spracherkennung verwenden", key="speech_stt_use")
            and _action("recognition/activate", {"model": model})
        ):
            st.rerun()
        if installed and active:
            st.success("Für deine Übungen ausgewählt")
        if installed:
            st.markdown("**Mikrofon ausprobieren**")
            st.caption(
                "Sprich einen Satz, zum Beispiel: „Ich möchte heute für meine Deutschprüfung üben.“ Maximal 20 Sekunden. Die Testaufnahme wird anschließend gelöscht."
            )
            recording = st.audio_input("Testsatz aufnehmen", sample_rate=16000, key="speech_mic_sample")
            if (
                st.button("Aufnahme lokal prüfen", disabled=recording is None, key="speech_stt_test")
                and recording is not None
            ):
                with st.spinner("Deine Aufnahme wird auf diesem Laptop erkannt…"):
                    try:
                        result = _call(
                            "recognition/test",
                            data={"model": model},
                            files={"file": ("test.wav", recording.getvalue(), "audio/wav")},
                            timeout=180,
                        )
                    except api.ApiError as exc:
                        show_error(exc)
                    else:
                        if result.get("text"):
                            st.write("**Das hat die App verstanden:**")
                            st.write(result["text"])
                            st.caption(
                                "Vergleiche den Text mit deinem Satz. Du kannst auch eine andere Erkennungsqualität ausprobieren."
                            )
                        else:
                            st.info("Keine Sprache erkannt. Sprich etwas näher am Mikrofon und versuche es erneut.")

    with st.container(border=True):
        st.markdown("### 2. Antworten anhören")
        st.write("Wähle, wie die App dir Antworten vorliest.")
        configured = settings.get("providers_configured") or {}
        active_voice = speech.get("tts_backend_active", "none")
        active_voice = {"openai": "openai_mini_tts", "google": "google_wavenet"}.get(active_voice, active_voice)
        st.caption("Aktuell: " + VOICES.get(active_voice, "Keine Stimme ausgewählt"))
        options = ["piper"]
        for backend, provider in [("openai_mini_tts", "openai"), ("voxtral_tts", "mistral")]:
            if configured.get(provider) or active_voice == backend:
                options.append(backend)
        if settings.get("google_credentials_file") or active_voice == "google_wavenet":
            options.append("google_wavenet")
        options.append("none")
        chosen = st.radio(
            "Vorlesen",
            options,
            index=options.index(active_voice) if active_voice in options else 0,
            format_func=lambda value: VOICES[str(value)],
            key="speech_voice_choice",
        )
        request: dict[str, Any] = {"backend": chosen}
        selected_voice = None
        current_voice = speech.get("openai_tts_voice") or speech.get("tts_voice")
        if current_voice not in ("marin", "cedar", "alloy"):
            current_voice = "marin"
        if chosen == "openai_mini_tts":
            selected_voice = st.selectbox(
                "Stimme",
                ["marin", "cedar", "alloy"],
                index=["marin", "cedar", "alloy"].index(current_voice),
                format_func=lambda value: {"marin": "Marin", "cedar": "Cedar", "alloy": "Alloy (bisherige Stimme)"}[
                    value
                ],
                key="speech_online_voice",
            )
            request["voice"] = selected_voice
            st.caption(
                "Liest deutsche Erklärungen und englische Fachbegriffe mit einer passenden Sprachvorgabe. Höre dir die Probe mit Cash und Accounts Receivable an."
            )
        current_speed = float(speech.get("tts_speed") or 0.85)
        speed = current_speed
        if chosen in ("piper", "openai_mini_tts"):
            speed = st.slider("Sprechtempo", 0.7, 1.2, current_speed, 0.05, format="%.2f×", key="speech_speed")
            st.caption("0,85×: langsamer fürs Lernen · 1,00×: normales Tempo")
            request["speed"] = speed
        selection_active = (
            chosen == active_voice
            and abs(speed - current_speed) < 0.001
            and (chosen != "openai_mini_tts" or selected_voice == current_voice)
        )
        ready = chosen != "piper" or bool(voice.get("ready"))
        if chosen == "piper":
            st.caption(
                "Bei englischen Fachbegriffen kann diese deutsche Stimme falsch betonen oder aussprechen. Dafür bietet sich die Online-Variante an."
            )
            st.caption(
                "Deutsche Stimme „Thorsten“. Einmal ca. 63 MB plus Zusatzkomponenten herunterladen; danach lokal, ohne Anmeldung beim Sprachanbieter."
            )
            if voice.get("state") == "error":
                st.error(voice.get("message", "Einrichtung fehlgeschlagen."))
            if (not ready or voice.get("state") == "error") and st.button(
                "Stimme herunterladen und einrichten",
                key="speech_voice_install",
                disabled=voice.get("state") == "installing",
            ):
                try:
                    api._request("POST", "/settings/local-voice", json={"accept_optional_install": True})
                except api.ApiError as exc:
                    show_error(exc)
                else:
                    st.session_state["speech_setup_was_busy"] = True
                    st.rerun()
            if ready:
                st.caption("✓ Deutsche Stimme auf diesem Laptop installiert")
        elif chosen != "none":
            st.caption(
                "Benötigt Internet und den verbundenen Anbieter. Zum Vorlesen wird der Antworttext an diesen Anbieter gesendet."
            )
            st.info(
                "Online-Vorlesen kostet pro Nutzung; auch die Hörprobe wird über den verbundenen Anbieter abgerechnet. "
                "Die Kostenübersicht zeigt deine geschätzten App-Kosten. Die tatsächliche Abrechnung kannst du dort separat prüfen."
            )
        if chosen == "piper":
            st.caption("Für das lokale Vorlesen fallen keine API-Gebühren an.")
        if st.button("Kostenübersicht öffnen", key="speech_costs"):
            st.switch_page("pages/kosten.py")
        if ready and chosen != "none" and st.button("Stimme anhören", key="speech_voice_test"):
            with st.spinner("Der Probesatz wird vorbereitet…"):
                try:
                    sample = _call("voice/test", json=request, raw=True, timeout=90)
                except api.ApiError as exc:
                    show_error(exc)
                else:
                    st.audio(sample, format="audio/wav" if chosen == "piper" else "audio/mpeg", autoplay=True)
        if selection_active and ready:
            st.success("Für deine Übungen ausgewählt" if chosen != "none" else "Vorlesen ist ausgeschaltet")
        elif ready and st.button("Auswahl verwenden", key="speech_voice_use") and _action("voice/activate", request):
            st.rerun()

    st.caption(
        "Lokale Spracherkennung und Stimme verarbeiten Audio auf dem Laptop. KI-Antworten und Bewertungen nutzen weiterhin deine verbundenen Anbieter."
    )
    with st.expander("Weitere Spracheinstellungen"):
        st.caption(
            "Die lokale Stimme verwendet Piper (GPL-3.0) und die deutsche Thorsten-Stimme. Zusatzkomponenten werden nur nach Klick auf „herunterladen und einrichten“ installiert."
        )
        google = st.text_input(
            "Google-Cloud-Zugangsdatei (nur bei bestehender Google-Stimme)",
            value=settings.get("google_credentials_file") or "",
            key="speech_google_path",
        )
        if st.button("Google-Zugang speichern", key="speech_google_save"):
            try:
                api.put_settings({"GOOGLE_APPLICATION_CREDENTIALS": google.strip() or None})
            except api.ApiError as exc:
                show_error(exc)
            else:
                st.rerun()
