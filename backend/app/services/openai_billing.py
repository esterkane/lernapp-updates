"""Read-only official OpenAI Costs and Completions Usage integration.

Admin credentials are request-scoped: never saved, returned, or logged. Provider totals
are separate from local estimates; no inferred allocation to a Lernapp workspace.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from time import monotonic
from typing import Any

import httpx


class BillingError(ValueError):
    pass


def _pages(client: httpx.Client, path: str, params: dict[str, Any], deadline: float) -> list[dict[str, Any]]:
    buckets: list[dict[str, Any]] = []
    seen = set()
    for _ in range(20):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise BillingError('Der Abruf dauert zu lange. Bitte einen kürzeren Zeitraum wählen.')
        try:
            response = client.get('https://api.openai.com/v1/organization/' + path, params=params,
                                  timeout=min(remaining, 15))
        except httpx.HTTPError as exc:
            raise BillingError('OpenAI ist gerade nicht erreichbar. Bitte erneut versuchen.') from exc
        if response.status_code in (401, 403):
            raise BillingError('OpenAI hat den Abrechnungszugriff abgelehnt. Erforderlich ist ein Admin-API-Schlüssel mit Leserechten für Nutzung und Kosten.')
        if response.status_code == 429:
            raise BillingError('Zu viele Abfragen. Bitte später erneut versuchen.')
        if response.status_code != 200:
            raise BillingError('Die Abrechnungsdaten konnten bei OpenAI nicht abgerufen werden.')
        try:
            body = response.json()
            if not isinstance(body['data'], list) or not isinstance(body['has_more'], bool):
                raise ValueError()
            buckets.extend(body['data'])
            if not body['has_more']:
                return buckets
            cursor = body.get('next_page')
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise ValueError()
            seen.add(cursor)
            params = {**params, 'page': cursor}
        except (ValueError, KeyError, TypeError) as exc:
            raise BillingError('OpenAI lieferte unvollständige Abrechnungsdaten. Es wird kein Gesamtbetrag geschätzt.') from exc
    raise BillingError('Zu viele Ergebnisse. Bitte einen kürzeren Zeitraum oder ein Projekt auswählen.')


def fetch(api_key: str, start: date, end: date, project_id: str | None = None,
          organization_id: str | None = None, api_key_id: str | None = None) -> dict[str, Any]:
    """end is an inclusive UTC calendar date. Costs can include adjustments/credits."""
    if not api_key.strip() or not 0 <= (end - start).days <= 92:
        raise BillingError('Bitte einen Schlüssel und einen Zeitraum von höchstens 93 Tagen angeben.')
    start_time = int(datetime.combine(start, datetime.min.time(), UTC).timestamp())
    end_time = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC).timestamp())
    params: dict[str, Any] = {'start_time': start_time, 'end_time': end_time, 'bucket_width': '1d'}
    if project_id:
        params['project_ids[]'] = [project_id]
    if api_key_id:
        params['api_key_ids[]'] = [api_key_id]
    headers = {'Authorization': 'Bearer ' + api_key.strip()}
    if organization_id:
        headers['OpenAI-Organization'] = organization_id
    deadline = monotonic() + 55
    with httpx.Client(headers=headers, follow_redirects=False) as client:
        costs = _pages(client, 'costs', {**params, 'group_by[]': ['project_id', 'line_item'], 'limit': 180}, deadline)
        total = Decimal(0)
        rows = []
        try:
            for bucket in costs:
                for result in bucket['results']:
                    amount = result['amount']
                    if str(amount['currency']).lower() != 'usd' or amount['value'] is None:
                        raise ValueError()
                    value = Decimal(str(amount['value']))
                    if not value.is_finite():
                        raise ValueError()
                    total += value
                    rows.append({'start_time': bucket['start_time'], 'project_id': result.get('project_id'),
                                 'line_item': result.get('line_item'), 'amount_usd': str(value)})
        except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
            raise BillingError('Unvollständige Beträge oder unerwartete Währung. Bitte das OpenAI-Dashboard prüfen.') from exc
        usage = None
        usage_error = None
        try:
            buckets = _pages(client, 'usage/completions', {**params, 'group_by[]': ['project_id', 'model'], 'limit': 31}, deadline)
            counts = {'input_tokens': 0, 'output_tokens': 0, 'input_cached_tokens': 0, 'num_model_requests': 0}
            for bucket in buckets:
                for result in bucket['results']:
                    for field in counts:
                        value = result.get(field)
                        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                            raise ValueError()
                        counts[field] += value
            usage = counts
        except (BillingError, ValueError, KeyError, TypeError):
            usage_error = 'Kosten wurden abgerufen; die separate Sprachmodell-Nutzungsstatistik ist nicht verfügbar.'
    return {'source': 'OpenAI Costs API', 'fetched_at': datetime.now(UTC).isoformat(),
            'period': {'from': start.isoformat(), 'to_inclusive': end.isoformat(), 'timezone': 'UTC'},
            'project_id': project_id, 'api_key_id': api_key_id, 'organization_id': organization_id,
            'currency': 'USD', 'total_usd': str(total), 'cost_rows': rows,
            'completion_usage': usage, 'usage_error': usage_error,
            'scope_note': 'OpenAI-Abrechnung für die ausgewählten Filter; kann Nutzung anderer Apps enthalten. Keine Zuordnung zu einzelnen Lernapp-Anfragen. Aktuelle Tage können sich noch ändern.'}
