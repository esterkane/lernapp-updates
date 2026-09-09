"""Kosten – monthly AI-cost dashboard for a non-technical learner (docs/api.md "Cost tracking v2", ADR-0019).

Primary view: expected cost, learning time, cost per learning hour, monthly limit, providers.
Tokens and per-request rows live only inside the "Erweiterte Kostendetails" expander.
Wording rule: "geschätzt" / "voraussichtlich" — never a claim about what the provider bills.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import streamlit as st
from lernapp_ui import api
from lernapp_ui.billing_ui import openai_billing
from lernapp_ui.components import (
    COST_TOOLTIP_DE,
    budget_warning_level,
    cost_status_label,
    datetime_de,
    duration_de,
    empty_state,
    money,
    number_de,
    pricing_tier_label,
    provider_label,
    service_label,
    show_error,
    usage_unit_label,
    usage_units_de,
)

st.title("Abrechnung & Nutzung")
st.caption("Für die Einrichtung und Verwaltung: Anbieterabrechnung und lokale Schätzungen getrennt prüfen.")

openai_billing()
st.divider()
st.subheader("Lokale Schätzungen – keine Anbieterabrechnung")

MONTH_NAMES_DE = [
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
]
EVENTS_PAGE_SIZE = 25
MONTHS_BACK = 12


# ------------------------------------------------------------------ month picker
def _month_options(today: date, n: int = MONTHS_BACK) -> list[str]:
    year, month = today.year, today.month
    out: list[str] = []
    for _ in range(n):
        out.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return out


def _month_label(value: str) -> str:
    year, month = value.split("-")
    return f"{MONTH_NAMES_DE[int(month) - 1]} {year}"


month = st.selectbox("Monat", _month_options(date.today()), format_func=_month_label, key="kosten_month")

# ------------------------------------------------------------------ load
with st.spinner("Kosten werden geladen…"):
    try:
        summary: dict[str, Any] = api.usage_summary(month=month)
    except api.ApiError as exc:
        show_error(exc, "Die Kostenübersicht konnte nicht geladen werden. " + api.HINT_CONNECTION)
        st.stop()

period: dict[str, Any] = summary.get("period") or {}
requests: dict[str, Any] = summary.get("requests") or {}
budget: dict[str, Any] = summary.get("budget") or {}
providers: list[dict[str, Any]] = summary.get("providers") or []
models: list[dict[str, Any]] = summary.get("models") or []
unknown_models: list[dict[str, Any]] = summary.get("unknown_models") or []

expected_total = float(summary.get("expected_cost_eur") or 0.0)
learning_seconds = float(summary.get("learning_seconds") or 0.0)
rate_available = bool(summary.get("rate_available"))
rate_value = summary.get("cost_per_learning_hour_eur")
n_total = int(requests.get("total") or 0)
n_free = int(requests.get("free") or 0)
n_unknown = int(requests.get("unknown") or 0)
limit_eur = budget.get("monthly_budget_eur")
used_eur = float(budget.get("used_eur") or expected_total)
share = budget.get("share")
warning_level = budget_warning_level(budget)
stop_on_limit = bool(budget.get("stop_on_limit"))

st.subheader(period.get("label_de") or _month_label(month))

# ------------------------------------------------------------------ primary card
with st.container(border=True):
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Geschätzte AI-Kosten", money(expected_total), help=COST_TOOLTIP_DE)
    c2.metric(
        "Lernzeit", duration_de(learning_seconds), help="Zeit, die du in diesem Monat mit Übungen verbracht hast."
    )
    if rate_available and rate_value is not None:
        c3.metric("Kosten pro Lernstunde", money(rate_value), help="Geschätzte Kosten geteilt durch deine Lernzeit.")
        if summary.get("rate_hint_de"):
            c3.caption(str(summary["rate_hint_de"]))
    else:
        c3.metric("Kosten pro Lernstunde", "Noch nicht genügend Daten")
        c3.caption(str(summary.get("rate_hint_de") or "ab 5 Minuten Lernzeit"))
    if limit_eur is not None:
        limit_value = float(limit_eur)
        c4.metric("Monatslimit", f"{money(used_eur)} von {money(limit_value)}")
        ratio = float(share) if share is not None else (used_eur / limit_value if limit_value > 0 else 0.0)
        ratio = max(0.0, min(ratio, 1.0))
        c4.progress(ratio, text=f"{number_de(ratio * 100, 0)} % verbraucht")
    else:
        c4.metric("Monatslimit", "Kein Limit")
        c4.caption("Kein Limit gesetzt – unter Einstellungen festlegen")

if limit_eur is not None:
    if warning_level >= 100:
        if stop_on_limit:
            st.error(
                "Dein Monatslimit ist erreicht. Neue AI-Anfragen sind bis zum Monatsende gestoppt – "
                "unter Einstellungen kannst du das Limit anpassen."
            )
        else:
            st.error(
                "Dein Monatslimit ist erreicht. Du kannst weiterlernen, die geschätzten Kosten laufen aber weiter – "
                "unter Einstellungen kannst du die Nutzung beim Limit stoppen lassen."
            )
    elif warning_level >= 95:
        st.error("Achtung: 95 % deines Monatslimits sind verbraucht.")
    elif warning_level >= 80:
        st.warning("Du hast 80 % deines Monatslimits verbraucht.")

# ------------------------------------------------------------------ state: empty / free / mixed
if n_total == 0:
    empty_state("Noch keine AI-Nutzung in diesem Monat.")
else:
    if n_free == n_total:
        st.success("Alle Anfragen liefen über die kostenlose Stufe.")

    st.markdown("**Nach Anbieter**")
    for row in providers:
        label = provider_label(row.get("provider"), row.get("label_de"))
        left, right = st.columns([3, 1])
        left.markdown(label)
        free_label = row.get("free_tier_label_de")
        if free_label:
            left.caption(str(free_label))
        right.markdown(f"**{money(row.get('expected_eur'))}**")

    if summary.get("status_note_de"):
        st.caption(str(summary["status_note_de"]))

    if n_unknown > 0:
        st.info(
            ("1 Anfrage hat" if n_unknown == 1 else f"{n_unknown} Anfragen haben")
            + " noch keine Preisinformation. Die geschätzten Kosten sind deshalb wahrscheinlich etwas zu niedrig."
        )
        with st.expander("Details anzeigen"):
            st.write(
                "Für diese Modelle fehlt ein Preis in der Preisliste der App. Die Anfragen wurden erfasst, "
                "aber noch nicht bewertet."
            )
            for um in unknown_models:
                missing = um.get("missing_units")
                if isinstance(missing, list | tuple):
                    missing_text = ", ".join(usage_unit_label(u) for u in missing) or "–"
                else:
                    missing_text = usage_unit_label(missing) if missing else "–"
                n_req = int(um.get("requests") or 0)
                st.markdown(
                    f"- **{provider_label(um.get('provider'))}** · {service_label(um.get('service'))} · "
                    f"Modell „{um.get('model') or '–'}“ · "
                    f"{'1 Anfrage' if n_req == 1 else f'{n_req} Anfragen'} · fehlende Preisangabe für: {missing_text}"
                )

# ------------------------------------------------------------------ advanced details
if n_total > 0:
    with st.expander("Erweiterte Kostendetails"):
        st.caption(
            "Für Neugierige: welche Modelle wie viel verbraucht haben. „Listenpreis“ ist der reguläre Preis des "
            "Anbieters für dieselbe Nutzung – auf einer kostenlosen Stufe zahlst du ihn nicht."
        )
        if models:
            model_table = pd.DataFrame(
                {
                    "Anbieter": [provider_label(m.get("provider")) for m in models],
                    "Dienst": [service_label(m.get("service")) for m in models],
                    "Modell": [str(m.get("model") or "–") for m in models],
                    "Anfragen": [int(m.get("requests") or 0) for m in models],
                    "Eingabe-Tokens": [int(m.get("input_tokens") or 0) for m in models],
                    "Ausgabe-Tokens": [int(m.get("output_tokens") or 0) for m in models],
                    "Audio (s)": [float(m.get("audio_seconds") or 0.0) for m in models],
                    "Zeichen": [int(m.get("characters") or 0) for m in models],
                    "Geschätzt (€)": [money(m.get("expected_eur")) for m in models],
                    "Listenpreis (€)": [money(m.get("list_eur")) for m in models],
                    "Ohne Preis": [int(m.get("unknown") or 0) for m in models],
                }
            )
            st.dataframe(model_table, hide_index=True, width="stretch")
        else:
            st.caption("Keine Modelle in diesem Monat.")

        # --- paginated event list
        st.markdown("**Einzelne Anfragen**")
        events_key = f"kosten_events_{month}"
        if events_key not in st.session_state:
            st.session_state[events_key] = {"items": [], "total": None, "offset": 0}
        events_state: dict[str, Any] = st.session_state[events_key]

        def _load_more() -> None:
            try:
                page = api.usage_events(
                    from_=period.get("from"),
                    to=period.get("to"),
                    limit=EVENTS_PAGE_SIZE,
                    offset=int(events_state["offset"]),
                )
            except api.ApiError as exc:
                st.caption(f"Die Anfragen konnten nicht geladen werden: {exc.message_de}")
                return
            items = page.get("items") or []
            events_state["items"].extend(items)
            events_state["offset"] = int(events_state["offset"]) + len(items)
            events_state["total"] = int(page.get("total") or len(events_state["items"]))

        if events_state["total"] is None:
            _load_more()

        items: list[dict[str, Any]] = events_state["items"]
        if items:
            event_table = pd.DataFrame(
                {
                    "Zeitpunkt": [datetime_de(e.get("ts")) for e in items],
                    "Anbieter": [provider_label(e.get("provider")) for e in items],
                    "Dienst": [service_label(e.get("service")) for e in items],
                    "Modell": [str(e.get("model") or "–") for e in items],
                    "Sitzung": [str(e.get("session_id") or "–")[:8] for e in items],
                    "Nutzung": [usage_units_de(e) for e in items],
                    "Tarif": [pricing_tier_label(e.get("pricing_tier")) for e in items],
                    "Status": [cost_status_label(e.get("cost_status")) for e in items],
                    "Geschätzt (€)": [money(e.get("expected_cost_eur")) for e in items],
                    "Listenpreis (€)": [money(e.get("list_cost_eur")) for e in items],
                }
            )
            st.dataframe(event_table, hide_index=True, width="stretch")
            total_events = int(events_state["total"] or 0)
            st.caption(f"{len(items)} von {total_events} Anfragen geladen.")
            if len(items) < total_events and st.button("Mehr laden", key=f"kosten_more_{month}"):
                _load_more()
                st.rerun()
        else:
            st.caption("Keine einzelnen Anfragen in diesem Monat.")

    # --- counterfactual (list-price comparison from the ledger)
    with st.expander("Vergleich: alles in der Cloud"):
        st.caption(
            "Vergleich nach Listenpreisen: Die Spracherkennung läuft auf deinem Gerät und kostet nichts. "
            "So sähe es aus, wenn auch sie über einen Cloud-Anbieter liefe."
        )
        variants = {
            "all_cloud_openai": "Alles über OpenAI",
            "cheapest_cloud": "Günstigster Cloud-Anbieter",
        }
        for variant, label in variants.items():
            try:
                cf = api.costs_counterfactual(variant, from_=period.get("from"), to=period.get("to"))
            except api.ApiError as exc:
                st.caption(f"{label}: konnte nicht berechnet werden ({exc.message_de})")
                continue
            st.markdown(
                f"- **{label}:** voraussichtlich **{money(cf.get('counterfactual_eur'))}** nach Listenpreis "
                f"statt {money(cf.get('actual_eur'))} – Ersparnis **{money(cf.get('saving_eur'))}**."
            )

# ------------------------------------------------------------------ footer
pricing_version = summary.get("pricing_version") or "–"
fx_rate = summary.get("fx_rate")
fx_text = f"1 US-Dollar = {number_de(fx_rate, 4)} €" if fx_rate else "Wechselkurs unbekannt"
st.caption(f"Preisliste Version {pricing_version} · {fx_text}")
