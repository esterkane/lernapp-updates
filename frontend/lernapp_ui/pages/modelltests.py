"""Model test import, review, practice and history; keys stay out of active attempts."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import streamlit as st
from lernapp_ui import api, exam_api

st.title("📚 Modelltests")
st.caption("Eigene Prüfungs-PDFs mit passenden Hördateien üben. Ergebnisse sind interne Übungsauswertungen.")


def sound(exam_id):
    key = f"exam_audio_{exam_id}"
    if key not in st.session_state:
        st.session_state[key] = exam_api.asset(exam_id, "audio")
    return st.session_state[key]


def practice(identifier):
    attempt = exam_api.attempt(identifier)
    test = attempt["test"]
    result = attempt.get("result")
    progress = attempt.get("progress") or {}
    bank_key = f"exam_answers_{identifier}"
    position_key = f"exam_position_{identifier}"
    revision_key = f"exam_progress_revision_{identifier}"
    error_key = f"exam_save_error_{identifier}"
    answers = st.session_state.setdefault(bank_key, dict(progress.get("answers", {})))
    st.session_state.setdefault(position_key, progress.get("position", 0))
    st.session_state.setdefault(revision_key, progress.get("revision", 0))

    def persist():
        if result:
            return True
        try:
            saved = exam_api.save_progress(identifier, dict(answers), st.session_state[position_key], st.session_state[revision_key])
        except api.ApiError as exc:
            st.session_state[error_key] = exc.message_de
            return False
        st.session_state[revision_key] = saved["revision"]
        st.session_state.pop(error_key, None)
        return True

    if st.button("← Zurück zur Übersicht", key=f"exam_overview_{identifier}") and persist():
        st.session_state.pop("exam_active", None)
        st.rerun()
    if st.session_state.get(error_key):
        st.error("Fortschritt noch nicht gespeichert: " + st.session_state[error_key])
        if st.button("Speichern erneut versuchen", key=f"exam_retry_{identifier}"):
            persist()
            st.rerun()
    st.subheader(test["title"])
    if attempt.get("outdated"):
        st.warning("Dieser Versuch enthält eine ältere Testfassung. Die Aufgaben wurden inzwischen korrigiert.")
        if st.button("Neue Übung mit korrigierten Aufgaben starten", type="primary"):
            st.session_state["exam_active"] = exam_api.start(attempt["exam_id"], False)["id"]
            st.rerun()
    if not result and test.get("duration_minutes"):

        @st.fragment(run_every="5s")
        def clock():
            elapsed = (datetime.now(UTC) - datetime.fromisoformat(attempt["started_at"])).total_seconds()
            remaining = max(0, int(test["duration_minutes"] * 60 - elapsed))
            st.metric("Verbleibende Zeit", f"{remaining // 60:02d}:{remaining % 60:02d}")
            if remaining == 0:
                st.warning("Zeit abgelaufen. Bitte abgeben; die Zeitüberschreitung wird im Ergebnis vermerkt.")

        clock()
    questions = test["questions"]
    if not questions:
        st.info("Dieser Test enthält noch keine Aufgaben.")
        return
    answered = sum(bool(value.strip()) for value in answers.values())
    if result:
        st.metric(result["label"], f"{result['score']:g} / {result['score_max']:g}")
        st.caption(f"Bearbeitungszeit: {result['elapsed_seconds'] / 60:.1f} Minuten")
        if result["time_exceeded"]:
            st.warning("Das eingestellte Zeitlimit wurde überschritten.")
    else:
        st.progress(answered / len(questions), text=f"{answered} von {len(questions)} Aufgaben beantwortet")
        st.caption("Antworten und aktuelle Aufgabe werden beim Ändern automatisch auf diesem Laptop gespeichert. Du kannst später fortsetzen – auch nach einem Neustart.")
        if test.get("duration_minutes"):
            st.caption("Bei Übungen mit Zeitlimit läuft die Zeit auch außerhalb des Tests weiter.")
        with st.expander("Fortschritt zurücksetzen"):
            confirmed = st.checkbox("Meine Antworten in diesem Versuch löschen und wieder bei Aufgabe 1 beginnen.", key=f"exam_reset_confirm_{identifier}")
            if st.button("Jetzt zurücksetzen", disabled=not confirmed, key=f"exam_reset_{identifier}"):
                exam_api.reset_progress(identifier, st.session_state[revision_key])
                for name in list(st.session_state):
                    if identifier in name and name != "exam_active":
                        del st.session_state[name]
                st.rerun()
    position = st.selectbox(
        "Aufgabe auswählen", range(len(questions)), key=position_key, on_change=persist,
        format_func=lambda i: f"{i + 1}. {questions[i]['section']}" + (" · beantwortet" if answers.get(questions[i]["id"]) else ""),
    )
    def move(step):
        st.session_state[position_key] = max(0, min(len(questions) - 1, position + step))
        persist()
    back, next_question = st.columns(2)
    back.button("← Vorherige Aufgabe", disabled=position == 0, on_click=move, args=(-1,))
    next_question.button("Weiter →", disabled=position == len(questions) - 1, on_click=move, args=(1,))
    shown_source_pages = set()
    shown_passages = set()
    marked = {q["id"]: q for q in result["items"]} if result else {}
    for q in [questions[position]]:
        st.subheader(f"Aufgabe {position + 1} von {len(questions)} · {q['section']}")
        if q.get("instructions"):
            st.write(q["instructions"])
        if q.get("passage") and q["passage"] not in shown_passages:
            shown_passages.add(q["passage"])
            with st.container(border=True):
                st.write(q["passage"])
        show_original = st.checkbox(
            "Originalseiten mit Abbildungen anzeigen", value=not bool(q.get("passage")),
            key=f"original_{identifier}_{q['id']}",
        ) if q.get("source_pages") else False
        for source_page in (q.get("source_pages", []) if show_original else []):
            if source_page in shown_source_pages:
                continue
            shown_source_pages.add(source_page)
            cache_key = f"exam_page_{identifier}_{source_page}"
            if cache_key not in st.session_state:
                st.session_state[cache_key] = exam_api.page_image(attempt["exam_id"], source_page, identifier)
            with st.expander(f"Originaltext / Abbildung – PDF-Seite {source_page}", expanded=True):
                st.image(st.session_state[cache_key], width="stretch")
        if test.get("audio_seconds"):
            if q.get("audio_start") is not None:
                st.audio(sound(attempt["exam_id"]), start_time=q["audio_start"], end_time=q.get("audio_end"))
                st.caption("Hörabschnitt zu dieser Aufgabe.")
            else:
                with st.expander("Hördatei zum Test öffnen"):
                    if st.checkbox("Hördatei abspielen", key=f"exam_play_{identifier}"):
                        st.audio(sound(attempt["exam_id"]))
                        st.caption("Gesamte Hördatei; es ist noch kein einzelner Abschnitt zugeordnet.")
        key = f"exam_answer_{identifier}_{q['id']}"
        def remember(question_id=q["id"], widget_key=key):
            st.session_state[bank_key][question_id] = st.session_state[widget_key] or ""
            persist()
        if not result and key not in st.session_state:
            st.session_state[key] = answers.get(q["id"]) or (None if q["kind"] == "choice" else "")
        if result:
            st.write(q["question"])
            item = marked[q["id"]]
            st.write("Deine Antwort: " + (item["answer"] or "—"))
            if item["correct"] is True:
                st.success("Richtig")
            elif item["correct"] is False:
                st.error("Richtige Antwort: " + " / ".join(item["expected"]))
            else:
                st.info("Offene Aufgabe: separat beurteilen; nicht in der automatischen Punktzahl enthalten.")
                if item["answer"] and q["kind"] in ("writing", "speaking"):
                    if st.button("KI-Feedback anfordern", key=f"feedback_{identifier}_{q['id']}"):
                        with st.spinner("Feedback wird erstellt…"):
                            fn = api.assess_writing if q["kind"] == "writing" else api.assess_speaking
                            feedback = fn(
                                item["answer"], task_text=q["instructions"] + "\n" + q["passage"] + "\n" + q["question"]
                            )
                            st.session_state[f"exam_feedback_{identifier}_{q['id']}"] = feedback
                    feedback = st.session_state.get(f"exam_feedback_{identifier}_{q['id']}")
                    if feedback:
                        st.write((feedback.get("rubric") or {}).get("summary_de") or feedback)
            if item.get("explanation"):
                st.write(item["explanation"])
        elif q["kind"] == "choice":
            answers[q["id"]] = st.radio(q["question"], q["options"], index=None, key=key, on_change=remember) or ""
        else:
            answers[q["id"]] = st.text_area(
                q["question"],
                key=key,
                max_chars=20000,
                on_change=remember,
                help="Bei Sprechaufgaben kannst du dein Transkript oder Notizen eintragen.",
            )
        st.divider()
    if not result:
        unanswered = len(questions) - sum(bool(value.strip()) for value in answers.values())
        if unanswered:
            st.caption(f"Noch {unanswered} Aufgaben offen. Du kannst sie über die Aufgabenauswahl erreichen.")
        if st.button("Test abgeben und auswerten", type="primary"):
            exam_api.submit(identifier, dict(answers))
            st.rerun()


def edit(selected):
    data = exam_api.editor(selected)
    draft = data["draft"]
    questions = draft.get("questions", [])
    st.subheader("Import zum Üben vorbereiten")
    if len(data.get("extracted_pages", [])) < len(data["pages"]):
        st.info(
            "Schritt 1 von 2: Die Dateien sind gespeichert. Klicke auf „Alle Aufgaben aus PDF erstellen“. "
            "Die App verarbeitet automatisch das gesamte PDF. Danach kannst du die Aufgaben prüfen und zum Üben freigeben."
        )
    else:
        st.info(f"Schritt 2 von 2: {len(questions)} Aufgaben sind vorbereitet. Prüfe sie unten mit dem Original.")
        checked = st.checkbox(
            "Ich habe Aufgaben, Lesetexte, Lösungen und Hörzuordnung mit dem Original geprüft; vor der Abgabe sind keine Lösungen sichtbar.",
            key=f"review_{selected}_{data['revision']}",
        )
        if st.button("Zum Üben freigeben", disabled=not checked, type="primary"):
            exam_api.save(selected, draft, True, data["revision"])
            st.session_state["exam_open_practice"] = selected
            st.rerun()
    completed = len(data.get("extracted_pages", []))
    total = len(data["pages"])
    if completed < total:
        st.caption(
            f"{completed} von {total} PDF-Seiten verarbeitet. Alle Abschnitte werden automatisch eingelesen. "
            "Das kann mehrere Minuten dauern und verursacht KI-Anbietergebühren. "
            "Bei einer Unterbrechung bleiben fertige Abschnitte gespeichert."
        )
        if st.button("Alle Aufgaben aus PDF erstellen" if not completed else "Alle Aufgaben weiter erstellen", type="primary"):
            exam_api.start_import(selected)
            st.rerun()

        @st.fragment(run_every="5s")
        def import_progress():
            job = exam_api.import_status(selected)
            st.progress(job["completed"] / job["total"], text=f"{job['completed']} von {job['total']} PDF-Seiten verarbeitet")
            if job["running"]:
                st.info("Import läuft im Hintergrund. Du kannst diese Seite verlassen. Langsame Seiten werden erneut versucht.")
            elif job.get("error"):
                st.warning(job["error"])
            elif job["completed"] == job["total"]:
                st.rerun(scope="app")

        import_progress()
    else:
        st.success(f"Alle {total} PDF-Seiten wurden verarbeitet. {len(questions)} Aufgaben erkannt.")
    st.caption(
        f"{len(data['pages'])} PDF-Seiten · Original und Lösungen sind nur hier im Bearbeitungsbereich sichtbar."
    )
    if st.button("Original-PDF bereitstellen"):
        st.session_state[f"exam_pdf_{selected}"] = exam_api.asset(selected, "pdf")
    if st.session_state.get(f"exam_pdf_{selected}"):
        st.download_button(
            "Original-PDF herunterladen",
            st.session_state[f"exam_pdf_{selected}"],
            file_name="modelltest.pdf",
            mime="application/pdf",
        )
    page = st.number_input("PDF-Seite ansehen", 1, len(data["pages"]), 1, key=f"page_{selected}")
    with st.expander("Extrahierter Seitentext"):
        st.text(data["pages"][page - 1] or "Kein Text erkannt. Bitte Original prüfen und Fragen manuell eingeben.")
    with st.expander("Optional: Einzelne Seiten erneut übernehmen"):
        st.caption(
            "Wähle höchstens 8 Aufgabenseiten pro Durchlauf. Der übrige PDF-Text dient als Kontext für Lesetexte und Lösungen. Anbietergebühren fallen an."
        )
        first = st.number_input("Erste Aufgabenseite", 1, len(data["pages"]), 1)
        last = st.number_input("Letzte Aufgabenseite", int(first), min(len(data["pages"]), int(first) + 7), int(first))
        if st.button("Aufgaben aus PDF erstellen", type="primary"):
            with st.spinner("Aufgaben werden übernommen…"):
                exam_api.extract(selected, first, last)
            st.rerun()
    for warning in draft.get("warnings", []):
        st.warning(warning)
    with st.expander("Hördatei hinzufügen oder ersetzen"):
        upload = st.file_uploader("MP3/WAV auswählen", type=["mp3", "wav"], key=f"attach_{selected}")
        if st.button("Hördatei speichern", disabled=upload is None):
            exam_api.attach_audio(selected, upload)
            st.session_state.pop(f"exam_audio_{selected}", None)
            st.rerun()
    if data.get("audio_seconds"):
        st.audio(sound(selected))
        st.caption("Hörabschnitte kannst du bei jeder Frage mit Start- und Endsekunden zuordnen.")
        with st.expander("Hörabschnitte automatisch vorschlagen"):
            st.caption(
                "Die Hördatei wird einmal transkribiert (je nach Anbieter mit Kosten). Die Zuordnung verwendet die hinterlegten Hörtext-Referenzen. Vorschläge bitte anhören und prüfen."
            )
            if st.button("Transkribieren und Zuordnung vorschlagen"):
                with st.spinner("Hördatei wird analysiert; das kann mehrere Minuten dauern…"):
                    exam_api.audio_suggestions(selected)
                st.rerun()
            suggestions = data.get("audio_suggestions", [])
            if data.get("transcript") and not suggestions:
                st.info(
                    "Keine sichere Textübereinstimmung gefunden. Bitte Hörtext-Referenzen ergänzen oder Zeitmarken manuell setzen."
                )
            for suggestion in suggestions:
                st.write(f"{suggestion['id']}: {suggestion['start']:.1f}–{suggestion['end']:.1f} Sekunden")
                st.caption(suggestion["excerpt"])
                st.audio(sound(selected), start_time=suggestion["start"], end_time=suggestion["end"])
                if st.button("Zeitmarken als Entwurf übernehmen", key=f"suggest_{selected}_{suggestion['id']}"):
                    updated = [
                        {**q, "audio_start": suggestion["start"], "audio_end": suggestion["end"]}
                        if q["id"] == suggestion["id"]
                        else q
                        for q in draft["questions"]
                    ]
                    exam_api.save(selected, {**draft, "questions": updated}, False, data["revision"])
                    st.rerun()
            if data.get("transcript"):
                st.text_area(
                    "Hörtranskript (nur Bearbeitungsbereich)", data["transcript"].get("text", ""), disabled=True
                )
    with st.form(f"meta_{selected}_{data['revision']}"):
        title = st.text_input("Testtitel", draft["title"])
        level = st.text_input("Niveau", draft.get("level", "B2"))
        duration = st.number_input("Zeitlimit in Minuten (0 = ohne)", 0, 300, draft.get("duration_minutes", 0))
        if st.form_submit_button("Testangaben speichern"):
            exam_api.save(
                selected,
                {**draft, "title": title, "level": level, "duration_minutes": duration},
                False,
                data["revision"],
            )
            st.rerun()
    questions = draft.get("questions", [])
    labels = [str(i) for i in range(len(questions))] + ["new"]
    choice = st.selectbox(
        "Frage bearbeiten",
        labels,
        format_func=lambda x: (
            "Neue Frage" if x == "new" else f"{questions[int(x)]['id']}: {questions[int(x)]['question'][:65]}"
        ),
        key=f"edit_q_{selected}",
    )
    q = {} if choice == "new" else questions[int(choice)]
    kinds = {"choice": "Auswahl", "text": "Kurzantwort", "writing": "Schreiben", "speaking": "Sprechen", "open": "Freie Antwort (manuelle Prüfung)"}
    with st.form(f"question_{selected}_{choice}_{data['revision']}"):
        section = st.text_input("Abschnitt", q.get("section", "Lesen"))
        kind = st.selectbox(
            "Antworttyp", list(kinds), index=list(kinds).index(q.get("kind", "choice")), format_func=kinds.get
        )
        question_page = st.number_input("Quellseite", 1, len(data["pages"]), q.get("page", 1))
        instructions = st.text_area("Anweisungen", q.get("instructions", ""))
        passage = st.text_area("Lesetext (keine Lösungen oder Hörtranskripte)", q.get("passage", ""))
        question = st.text_area("Frage / Aufgabenstellung", q.get("question", ""))
        options = st.text_area("Antwortoptionen – eine pro Zeile", "\n".join(q.get("options", [])))
        answers = st.text_area(
            "Bestätigte Lösung / zulässige Varianten – eine pro Zeile", "\n".join(q.get("answers", []))
        )
        explanation = st.text_area("Erklärung nach Abgabe", q.get("explanation", ""))
        audio_reference = st.text_area(
            "Passender Ausschnitt aus dem Hörtranskript (im Test verborgen)", q.get("audio_reference", "")
        )
        points = st.number_input("Punkte", 0.1, 100.0, float(q.get("points", 1)))
        use_audio = st.checkbox(
            "Hörabschnitt zuordnen", value=q.get("audio_start") is not None, disabled=not data.get("audio_seconds")
        )
        start = st.number_input("Audio-Start in Sekunden", 0.0, value=float(q.get("audio_start") or 0))
        end = st.number_input("Audio-Ende in Sekunden (0 = bis Dateiende)", 0.0, value=float(q.get("audio_end") or 0))
        if st.form_submit_button("Frage speichern"):
            item = {
                "id": q.get("id", uuid4().hex[:12]),
                "section": section,
                "kind": kind,
                "page": question_page,
                "instructions": instructions,
                "passage": passage,
                "question": question,
                "options": [x.strip() for x in options.splitlines() if x.strip()],
                "answers": [x.strip() for x in answers.splitlines() if x.strip()],
                "explanation": explanation,
                "audio_reference": audio_reference,
                "source_pages": q.get("source_pages", []),
                "points": points,
                "audio_start": start if use_audio else None,
                "audio_end": end if use_audio and end else None,
            }
            updated = list(questions)
            if choice == "new":
                updated.append(item)
            else:
                updated[int(choice)] = item
            exam_api.save(selected, {**draft, "questions": updated}, False, data["revision"])
            st.rerun()
    if choice != "new" and st.button("Ausgewählte Frage entfernen"):
        exam_api.save(
            selected,
            {**draft, "questions": [q for i, q in enumerate(questions) if i != int(choice)]},
            False,
            data["revision"],
        )
        st.rerun()
    with st.expander("Test löschen"):
        confirm = st.checkbox("Originale, Entwurf und alle Versuche dieses Tests löschen")
        if st.button("Endgültig löschen", disabled=not confirm):
            exam_api.delete(selected)
            for key in list(st.session_state):
                if str(key).startswith(("exam_pdf_", "exam_audio_")):
                    del st.session_state[key]
            st.rerun()


try:
    if st.session_state.get("exam_active"):
        practice(st.session_state["exam_active"])
        st.stop()
    pending = st.session_state.pop("exam_open_editor", None)
    if pending:
        st.session_state["exam_mode"] = "Bearbeiten"
        st.session_state["exam_select_id_Bearbeiten"] = pending
    ready = st.session_state.pop("exam_open_practice", None)
    if ready:
        st.session_state["exam_mode"] = "Üben"
        st.session_state["exam_select_id_Üben"] = ready
    management = st.session_state.get("exam_mode") in ("Importieren", "Bearbeiten")
    if management:
        mode = st.session_state["exam_mode"]
        if st.button("← Zurück zu den Tests"):
            st.session_state["exam_mode"] = "Üben"
            st.rerun()
    else:
        mode = st.radio("Bereich", ["Üben", "Ergebnisse"], horizontal=True, key="exam_mode")
        with st.expander("Eigene Tests hinzufügen oder bearbeiten"):
            st.button("PDF und Hördatei hinzufügen", on_click=lambda: st.session_state.update(exam_mode="Importieren"))
            st.button("Gespeicherten Test bearbeiten", on_click=lambda: st.session_state.update(exam_mode="Bearbeiten"))
    if mode == "Importieren":
        st.subheader("PDF und passende Hördatei")
        st.caption("Dateien importieren → Aufgaben aus PDF erstellen → prüfen und zum Üben freigeben.")
        with st.form("exam_upload"):
            title = st.text_input("Titel (optional)")
            pdf = st.file_uploader("Prüfungs-PDF (bis 25 MB)", type=["pdf"])
            audio = st.file_uploader("Passende MP3/WAV (optional, bis 100 MB)", type=["mp3", "wav"])
            if st.form_submit_button("Dateien importieren und Aufgaben vorbereiten"):
                if pdf is None:
                    st.warning("Bitte eine Prüfungs-PDF auswählen.")
                else:
                    with st.spinner("Dateien werden eingelesen…"):
                        item = exam_api.upload(pdf, audio, title)
                    st.session_state["exam_open_editor"] = item["id"]
                    st.rerun()
        st.subheader("TANDEM München")
        st.markdown("[Original-Downloads öffnen](https://www.tandem-muenchen.de/de/downloads.html)")
        entries = exam_api.sources()
        source = st.selectbox("Modelltest mit Hördatei", entries, format_func=lambda x: x["title"])
        st.caption(
            "PDF und MP3 werden anhand der Zuordnung auf der Quellseite gemeinsam importiert. Ausgabe und Hörabschnitte anschließend prüfen."
        )
        if st.button("TANDEM-Dateien importieren"):
            with st.spinner("PDF und MP3 werden heruntergeladen…"):
                item = exam_api.import_source(source["id"])
            st.session_state["exam_open_editor"] = item["id"]
            st.rerun()
    elif mode == "Ergebnisse":
        history = exam_api.history()
        if not history:
            st.info("Noch keine Versuche gespeichert.")
        for item in history:
            st.write(
                f"{item['title']} · {item['started_at'][:16]} · "
                + (f"{item['score']} / {item['score_max']}" if item["submitted_at"] else "Noch nicht abgegeben")
            )
            if st.button("Ergebnis ansehen" if item["submitted_at"] else "Fortsetzen", key=item["id"]):
                st.session_state["exam_active"] = item["id"]
                st.rerun()
    else:
        tests = [t for t in exam_api.listing() if t.get("material_kind", "test") in ("test", "study")]
        if not tests:
            st.info(
                "Noch keine freigegebenen Tests. Unter Importieren Dateien hinzufügen und unter Bearbeiten prüfen."
                if mode == "Üben"
                else "Bitte zuerst eine PDF importieren."
            )
            st.stop()
        from lernapp_ui.library import unique_materials
        tests = unique_materials(tests)
        if mode == "Üben":
            category = st.radio("Was möchtest du üben?", ["Alle", "Prüfungen", "Kurze Übungen"], horizontal=True)
            if category != "Alle":
                kind = "study" if category == "Kurze Übungen" else "test"
                tests = [t for t in tests if t.get("material_kind", "test") == kind]
            query = st.text_input("Test suchen", placeholder="Titel oder Niveau, z. B. B2")
            tests = [t for t in tests if query.casefold() in (t["title"] + " " + str(t.get("level", ""))).casefold()]
        if not tests:
            st.info("Kein passender Test. Ändere die Suche oder füge eine PDF hinzu.")
            st.stop()
        by_id = {t["id"]: t for t in tests}

        def test_label(identifier):
            test = by_id[identifier]
            status = (
                "Bereit"
                if test["reviewed"]
                else ("Aufgaben fehlen" if not test["question_count"] else "Entwurf – Prüfung ausstehend")
            )
            return f"{test['title']} · {test['question_count']} Fragen · {status}"

        selected_id = st.selectbox("Modelltest", list(by_id), format_func=test_label, key=f"exam_select_id_{mode}")
        selected = by_id[selected_id]
        if mode == "Bearbeiten":
            edit(selected["id"])
        elif not selected["reviewed"]:
            if not selected["question_count"]:
                st.info(
                    "PDF und Hördatei sind gespeichert. Es fehlen noch die interaktiven Aufgaben. Übernimm sie aus dem PDF und prüfe anschließend die Lösungen."
                )
            else:
                st.info(
                    "Die Aufgaben sind vorbereitet. Prüfe die Lösungen und Hörzuordnung und gib den Test anschließend zum Üben frei."
                )
            if st.button(
                "Aufgaben vorbereiten" if not selected["question_count"] else "Entwurf prüfen und freigeben",
                type="primary",
            ):
                st.session_state["exam_open_editor"] = selected["id"]
                st.rerun()
        else:
            unfinished = [a for a in exam_api.history() if a.get("exam_id") == selected["id"] and not a.get("submitted_at")]
            if unfinished:
                latest = unfinished[0]
                st.info(f"Begonnene Übung: {latest.get('answered', 0)} Antworten gespeichert.")
                if st.button("Gespeicherte Übung fortsetzen", type="primary"):
                    st.session_state["exam_active"] = latest["id"]
                    st.rerun()
            timed = st.checkbox("Mit dem hinterlegten Zeitlimit üben")
            st.caption(
                "Nicht beantwortete objektive Fragen zählen als falsch. Offene Aufgaben werden separat beurteilt."
            )
            if st.button("Neue Übung starten" if unfinished else "Test starten", type="secondary" if unfinished else "primary"):
                st.session_state["exam_active"] = exam_api.start(selected["id"], timed)["id"]
                st.rerun()
except api.ApiError as exc:
    st.error(exc.message_de)
