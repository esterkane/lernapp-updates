---
name: speaking_feedback
version: 1.0.0
tier: assessment
schema: RubricResult
changelog: initial
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
Antworte NUR mit JSON nach RubricResult.
