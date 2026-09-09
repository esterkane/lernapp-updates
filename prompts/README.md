# Versioned prompts (ADR-0012)

`<name>.v<MAJOR.MINOR.PATCH>.md` with YAML front-matter. Never edit a released version — use
`/add-prompt-version`. `app/core/prompts.py` loads the highest version unless pinned in
`config/models.yaml: prompt_pins`. The version string is stored with every result and every
ledger row. Prompts are in German (product language); placeholders use `{{double_braces}}`.
