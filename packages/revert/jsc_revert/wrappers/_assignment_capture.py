"""Read assignment IDs in the explicit org before and after the wrapped write."""
import re

from jsc_common.soql import soql_literal
from . import _common as c


def assignment_query(org, names, users, *, license=False):
    users = list(users)
    if not users:
        if not org.username:
            return None
        users = [org.username]
    elif any('@' not in user for user in users):
        # The write still accepts aliases. Capture stays unknown rather than
        # reading machine-wide aliases or persisting credential-bearing display.
        return None
    literals = lambda values: ','.join("'" + soql_literal(value) + "'" for value in values)
    name_list = literals(names)
    obj = 'PermissionSetLicenseAssign' if license else 'PermissionSetAssignment'
    selector = (f'(PermissionSetLicense.DeveloperName IN ({name_list}) OR PermissionSetLicense.MasterLabel IN ({name_list}))'
                if license else f'PermissionSet.Name IN ({name_list})')
    return f'SELECT Id FROM {obj} WHERE Assignee.Username IN ({literals(users)}) AND {selector}'


def read_ids(target_org, query, prefix):
    if query is None:
        return None
    code, stdout, _ = c.run_sf_subprocess(
        ['sf', 'data', 'query', '--target-org', target_org, '--query', query, '--json'], timeout_seconds=60)
    data = c.parse_sf_json_safely(stdout)
    result = data.get('result') if isinstance(data, dict) else None
    if code or not isinstance(result, dict) or result.get('done') is not True:
        return None
    rows = result.get('records')
    if not isinstance(rows, list) or type(result.get('totalSize')) is not int or len(rows) != result['totalSize']:
        return None
    if any(not isinstance(row, dict) or not isinstance(row.get('Id'), str)
           or not re.fullmatch(prefix + r'[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?', row['Id']) for row in rows):
        return None
    return {row['Id'][:15]: row['Id'] for row in rows}


def record_difference(payload, before, after, kind):
    # An unavailable/truncated query is unknown, never an empty before-set.
    payload['assignment_before_ids'] = list(before.values()) if before is not None else None
    payload['assignment_after_ids'] = list(after.values()) if after is not None else None
    if before is not None and after is not None:
        payload[f'created_{kind}_ids'] = sorted(value for key, value in after.items() if key not in before)
        payload[f'{kind}_id_capture_status'] = 'captured'
