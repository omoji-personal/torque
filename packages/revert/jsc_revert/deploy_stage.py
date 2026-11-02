"""Retained temporary projects belong to one snapshot, never to the caller."""
import json
import shutil
import sys
import tempfile
from pathlib import Path

from . import bundle


def retain(stage: str, manifest: dict) -> None:
    bundle.atomic_write_json(Path(stage) / '.torque-stage.json', {'snapshot_id': manifest['snapshot_id']})
    manifest['payload']['staged_project'] = stage


def cleanup(manifest: dict) -> None:
    """Remove only our marked temporary project after a confirmed terminal job."""
    if manifest.get('snapshot_status') not in ('complete', 'failed', 'applied_partial', 'abandoned'):
        return
    value = manifest.get('payload', {}).get('staged_project')
    if not value:
        return
    stage = Path(value)
    try:
        # Validate the resolved deletion target before recursive removal. Never
        # remove report_cwd: it may be an ordinary caller-owned SFDX project.
        if (not stage.is_absolute() or stage.is_symlink() or not stage.name.startswith('tqds-')
                or stage.resolve().parent != Path(tempfile.gettempdir()).resolve()):
            return
        marker = stage / '.torque-stage.json'
        if not stage.exists():
            return
        if json.loads(marker.read_text(encoding='utf-8')) != {'snapshot_id': manifest['snapshot_id']}:
            return
        shutil.rmtree(stage)
    except (OSError, ValueError, KeyError) as exc:
        print(f'warning: retained deploy project cleanup failed: {exc}', file=sys.stderr)
