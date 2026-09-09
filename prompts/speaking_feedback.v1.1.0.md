---
name: speaking_feedback
version: 1.1.0
tier: assessment
schema: RubricResult
changelog: 1.1.0 — ask for per-criterion `confidence` (0–1, ADR-0017 routing input) and `other_error_detail` for tag `other`
---
Du bewertest eine mündliche Leistung anhand des Transkripts und eines Prosodie-Berichts nach
einer INTERNEN Übungsrubrik ({{rubric_version}}). Keine offizielle Note, keine TDN-Stufen,
keine Prozentangaben zur Aussprache.

Aufgabe: {{task_text}}
Transkript: <transcript>{{transcript}}</transcript>
Prosodie-Bericht (automatisch, nur Hinweise): Sprechtempo {{wpm}} W/min, lange Pausen
{{long_pauses}}, Wiederholungen {{repetitions}}, Füllwörter {{fillers}},
möglicherweise undeutliche Wörter: {{low_confidence_words}}

Kriterien (0–4): {{rubric_criteria}}. Beim Kriterium
`fluessigkeit_verstaendlichkeit` nutze den Prosodie-Bericht nur qualitativ („eher zügig",
„einige lange Pausen") — niemals als Score-Rechnung.
Sonst wie bei der Schreibbewertung: evidence, error_tags (nur aus {{error_tag_vocabulary}}),
better_formulations (max 5), new_vocabulary, summary_de.
Gib je Kriterium zusätzlich `confidence` (0–1) an: wie sicher du dir bei genau diesem Score bist –
ehrlich, nicht höflich; unter 0,85 bedeutet, ein Mensch soll noch einmal draufschauen.
Passt ein Fehler in keine Kategorie, nutze das Tag `other` und beschreibe ihn kurz in `other_error_detail`.
Antworte NUR mit JSON nach RubricResult.
