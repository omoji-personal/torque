"""Shared bulk input parsing and lossless before-state/replay artifacts."""
import csv
import json
import io
import re
import sys
from pathlib import Path
from .. import bundle


def parse_identifiers(path: Path, identifier: str, *, allow_blank=False) -> tuple[list[str], list[str]]:
    with path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])
        by_lower = {field.lower(): field for field in fields}
        if len(by_lower) != len(fields) or identifier.lower() not in by_lower:
            raise ValueError(f'CSV must have an unambiguous {identifier} header')
        key = by_lower[identifier.lower()]
        values = []
        for row in reader:
            value = row.get(key)
            if None in row or (not allow_blank and (not value or not value.strip())):
                raise ValueError(f'CSV row {reader.line_num} has no {identifier} or has extra cells')
            value = value or ''
            if identifier.lower() == 'id' and value and not re.fullmatch(r'[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?', value):
                raise ValueError(f'CSV row {reader.line_num} has an invalid Id (expected 15 or 18 alphanumeric characters)')
            values.append(value)
        if not any(value.strip() for value in values):
            raise ValueError('CSV has no identifiers to capture')
    return values, fields


def capture_fields(fields: list[str]) -> list[str]:
    selected = ['Id'] + [field for field in fields if field.lower() != 'id']
    for field in fields:
        if '.' in field:
            parent_id = field.rsplit('.', 1)[0] + '.Id'
            if parent_id.lower() not in {name.lower() for name in selected}:
                selected.append(parent_id)
    return selected


def write_capture(path: Path, fields: list[str], records: list[dict]) -> bool:
    """Require all selected keys; absent keys must never become null restores."""
    requested = capture_fields(fields)
    replay_count = 1 + sum(field.lower() != 'id' for field in fields)
    def lookup(record, field):
        parts, names = field.split('.'), []
        for part in parts:
            if record is None:
                names.append(part)
                continue
            keys = {key.lower(): key for key in record} if isinstance(record, dict) else {}
            if part.lower() not in keys:
                raise ValueError(f'Cannot capture CSV column {field!r}: missing from query response')
            name = keys[part.lower()]
            names.append(name)
            record = record[name]
        return '.'.join(names), record
    rows = []
    try:
        names = [lookup(records[0], field)[0] for field in requested]
        names[0] = 'Id'
        for record in records:
            row = {name: lookup(record, field)[1] for name, field in zip(names, requested)}
            if not row['Id']:
                raise ValueError('Cannot capture CSV column Id: query returned an empty Id')
            rows.append(row)
    except ValueError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return False
    bundle.atomic_write_bytes(path.with_suffix('.json'), (json.dumps({'fields': names, 'records': rows}, indent=2) + '\n').encode('utf-8'))
    for target, replay in ((path, False), (path.with_name('replay.csv'), True)):
        stream = io.StringIO(newline='')
        output_names = names[:replay_count] if replay else names
        writer = csv.DictWriter(stream, fieldnames=output_names, lineterminator='\n')
        writer.writeheader()
        for row in rows:
            # A null relationship must be cleared with its actual lookup field,
            # not with a null external-ID value. Keep that manual step explicit.
            writer.writerow({key: '#N/A' if value is None and replay and '.' not in key else
                             str(value).lower() if isinstance(value, bool) else value
                             for key, value in row.items() if key in output_names})
        bundle.atomic_write_bytes(target, stream.getvalue().encode('utf-8'))
    return True
