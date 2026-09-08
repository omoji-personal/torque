"""Interpret observed Salesforce jobs without turning missing evidence green.

Supports the nested Bulk API response and current Salesforce CLI's flat success
and error.data envelopes. Pure parsing; the caller owns any follow-up query.
"""
from __future__ import annotations

import re
import shlex


def _containers(data):
    if not isinstance(data, dict):
        return []
    roots = [data.get('result'), data.get('data')]
    result = data.get('result')
    if isinstance(result, dict) and 'statusCode' in result:
        if type(result.get('statusCode')) is not int or result['statusCode'] != 200 or not isinstance(result.get('body'), dict):
            return []
        roots = [result['body'], data.get('data')]
    if isinstance(data.get('error'), dict):
        roots += [data['error'].get('data')]
    rows = [row for row in roots if isinstance(row, dict)]
    # Current CLI JobFailedError may retain an ID only in its suggested results
    # command. Parse identity from that recognized shape; never execute prose or
    # trust its target-org argument. A caller must read the ID using its own org.
    if data.get('name') in ('JobFailedError', 'BulkJobError') and data.get('context') in (
            'DataImportBulk', 'DataUpdateBulk', 'DataUpsertBulk', 'DataDeleteBulk'):
        actions = data.get('actions')
        if isinstance(actions, list):
            for action in actions:
                if not isinstance(action, str):
                    continue
                for command in re.findall(r'sf data bulk results[^"\n]+', action):
                    try:
                        tokens = shlex.split(command)
                    except ValueError:
                        continue
                    for i, token in enumerate(tokens[:-1]):
                        if token == '--job-id':
                            rows.append({'jobId': tokens[i + 1]})
    return rows + [row['jobInfo'] for row in rows if isinstance(row.get('jobInfo'), dict)]


def _one(rows, names):
    values = [row[name] for row in rows for name in names if name in row and row[name] is not None]
    if not values:
        return None
    if any(type(value) is not type(values[0]) or value != values[0] for value in values):
        raise ValueError('Contradictory job evidence')
    return values[0]


def _job_id(rows, prefix, expected=None):
    values = [row[name] for row in rows for name in ('id', 'jobId') if row.get(name)]
    if expected:
        values.append(expected)
    if not values:
        return None
    if any(not isinstance(value, str) or not re.fullmatch(prefix + r'[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?', value) for value in values):
        raise ValueError('Invalid job identity')
    if len({value[:15] for value in values}) != 1:
        raise ValueError('Conflicting job identities')
    return max(values, key=len)


def _count(rows, names):
    normalized = []
    for row in rows:
        for name in names:
            if name not in row or row[name] is None:
                continue
            value = row[name]
            if isinstance(value, bool) or not isinstance(value, (int, str)) or not re.fullmatch(r'\d+', str(value)):
                raise ValueError('Invalid record count')
            normalized.append({name: int(value)})
    return _one(normalized, names)


def _consistent_deploy_details(rows):
    """Reject contradictory supplied details while allowing summary responses."""
    test_results = []
    for row in rows:
        details = row.get('details')
        if details is None:
            continue
        if not isinstance(details, dict):
            raise ValueError('Malformed deploy details')
        failures = details.get('componentFailures')
        if failures is not None and failures != []:
            raise ValueError('Component failures contradict deploy success')
        tests = details.get('runTestResult')
        if tests is None:
            continue
        if not isinstance(tests, dict):
            raise ValueError('Malformed deploy test details')
        failures = tests.get('failures')
        if failures is not None and failures != []:
            raise ValueError('Test failures contradict deploy success')
        for name in ('numFailures', 'numberFailures', 'numTestsRun'):
            value = tests.get(name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError('Invalid deploy test count')
        test_results.append(tests)
    failures = _one(test_results, ('numFailures', 'numberFailures'))
    if failures not in (None, 0):
        raise ValueError('Test failure count contradicts deploy success')
    completed = _count(rows, ('numberTestsCompleted',))
    total = _count(rows, ('numberTestsTotal',))
    ran = _one(test_results, ('numTestsRun',))
    known = [value for value in (completed, total, ran) if value is not None]
    if known and any(value != known[0] for value in known):
        raise ValueError('Contradictory deploy test completion counts')


def deploy_outcome(data, exit_code=0, expected_job_id=None):
    """Return (snapshot status, job ID). Unknown is partial, never complete."""
    rows = _containers(data)
    job_id = None
    try:
        job_id = _job_id(rows, '0Af', expected_job_id)
        if exit_code is not None and exit_code < 0:
            return ('pending_finalize_required' if job_id else 'partial'), job_id
        state = _one(rows, ('status',))
        done = _one(rows, ('done',))
        success = _one(rows, ('success',))
        if any(value is not None and type(value) is not bool for value in (done, success)):
            return 'partial', job_id
        if state in ('InProgress', 'Pending', 'Canceling'):
            return ('pending_finalize_required' if job_id else 'partial'), job_id
        if state == 'Canceled' and done is True and success is not True:
            return 'abandoned', job_id
        if state == 'Failed' and done is True and success is not True:
            return 'failed', job_id
        if state == 'SucceededPartial' and job_id and done is True:
            return 'applied_partial', job_id
        if state == 'Succeeded' and job_id and done is True and success is True:
            _consistent_deploy_details(rows)
            errors = _count(rows, ('numberComponentErrors',))
            test_errors = _count(rows, ('numberTestErrors',))
            if errors in (None, 0) and test_errors in (None, 0):
                return 'complete', job_id
    except ValueError:
        pass
    return 'partial', job_id


def bulk_outcome(data, exit_code=0, expected_job_id=None):
    """Return (snapshot status, job ID), retaining IDs from CLI error.data.

    A JobComplete error without counts needs an exact-job results query. The
    current CLI's successful synchronous ingest has counts and no state field.
    A jobId alone establishes submission, never completion.
    """
    rows = _containers(data)
    job_id = None
    try:
        job_id = _job_id(rows, '750', expected_job_id)
        if exit_code is not None and exit_code < 0:
            return ('pending_finalize_required' if job_id else 'partial'), job_id
        state = _one(rows, ('state', 'status'))
        if state in ('Open', 'UploadComplete', 'InProgress', 'Pending', 'Queued'):
            return ('pending_finalize_required' if job_id else 'partial'), job_id
        if not job_id:
            return 'partial', None
        if state not in (None, '', 'JobComplete', 'Succeeded', 'Completed', 'Failed', 'Aborted'):
            return 'partial', job_id
        processed = _count(rows, ('numberRecordsProcessed', 'processedRecords'))
        failed = _count(rows, ('numberRecordsFailed', 'failedRecords'))
        successful = _count(rows, ('successfulRecords',))
        if processed is None or failed is None:
            return 'pending_finalize_required', job_id
        if failed > processed or (successful is not None and successful != processed - failed):
            return 'partial', job_id
        if state in (None, '') and exit_code != 0:
            return 'partial', job_id
        if state in ('Failed', 'Aborted'):
            return ('applied_partial' if processed > failed else 'failed'), job_id
        if failed:
            return ('applied_partial' if processed > failed else 'failed'), job_id
        return 'complete', job_id
    except ValueError:
        return 'partial', job_id
