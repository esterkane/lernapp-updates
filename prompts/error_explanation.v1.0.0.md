---
name: error_explanation
version: 1.0.0
tier: conversation
schema: null
changelog: initial
---
Die Lernende hat bei einer Lese-/Höraufgabe folgende Items falsch beantwortet. Die Bewertung
ist bereits erfolgt (deterministisch) — du erklärst nur, warum die richtige Lösung richtig ist
und woran man sie im Text/Transkript erkennt. Keine neue Bewertung, keine Noten.

Text/Transkript: <source>{{source_text}}</source>
Falsche Items: {{wrong_items_json}}

Pro Item: 2–3 Sätze auf Deutsch, mit der Textstelle als Beleg, und ein Lesetipp
(z. B. Signalwörter, Umformulierungen erkennen).
