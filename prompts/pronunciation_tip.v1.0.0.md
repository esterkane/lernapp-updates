---
name: pronunciation_tip
version: 1.0.0
tier: conversation
schema: PronunciationTip
changelog: initial
---
Aus diesem automatischen Prosodie-/Aussprache-Bericht formulierst du GENAU EINEN konkreten,
freundlichen Tipp auf Deutsch (max. 2 Sätze) und nennst bis zu 3 Wörter zum Üben.
Keine Prozentzahlen, keine Scores, keine Diagnosen; formuliere Hinweise als „möglicherweise".

Bericht: {{prosody_report_json}}
Phonem-Hinweise (falls vorhanden): {{phone_flags_json}}

Antworte NUR mit JSON: {"tip_de": "...", "practice_words": ["..."]}
