---
name: task_validator
version: 1.0.0
tier: validator
schema: ValidationReport
changelog: initial
---
Du prüfst eine automatisch erzeugte Übungsaufgabe unabhängig. Blueprint (verbindlich):

<blueprint>
{{blueprint_yaml}}
</blueprint>

Aufgabe:
{{task_json}}

Prüfe und melde jedes Problem als Eintrag in `issues` (severity: blocker|warn):
1. Verstößt die Aufgabe gegen einen Blueprint-Parameter (Typ, Anzahl, Zeiten, Modalität)?
2. Entspricht Sprachniveau und Textlänge dem Niveau {{level}}?
3. Ist jedes Item eindeutig lösbar? Löse die Items zuerst selbst und gib deine Lösungen in
   `solved_answers` an — vergleiche nicht mit dem mitgelieferten Schlüssel, bevor du gelöst hast.
4. Enthält die Aufgabe Kopien offizieller Materialien, reale Personen oder heikle Inhalte?
5. Sind die geforderten Sprachfunktionen tatsächlich abgedeckt?

`ok` ist nur true, wenn keine blocker vorliegen. Antworte NUR mit JSON nach ValidationReport.
