"""User-owned paths. Loading never creates files or contacts a service."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat

ROLES = ('client', 'server', 'config')


class UserConfigError(ValueError):
    pass


def _path(value):
    if (not isinstance(value, str) or not value.strip()
            or re.search(r'[<>\x00]|\$\{|REPLACE_ME|YOUR[_-]|(?:^|[/\\])EXAMPLE(?:[/\\]|$)', value, re.I)
            or not Path(value).is_absolute()):
        raise UserConfigError('PROFILE_PATH_INVALID')
    path = Path(value)
    if path == Path(path.anchor):
        raise UserConfigError('PROFILE_PATH_INVALID')
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or (ancestor.exists() and
                getattr(ancestor.stat(follow_symlinks=False), 'st_file_attributes', 0)
                & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
            raise UserConfigError('PROFILE_PATH_REPARSE_POINT')
    if path.exists() and not path.is_dir():
        raise UserConfigError('PROFILE_DIRECTORY_REQUIRED')
    return path.resolve()


def load_profile(required_roles=()):
    locator = os.environ.get('K1_REVIEW_PROFILE', '').strip()
    if not locator:
        raise UserConfigError('USER_PROFILE_REQUIRED')
    if not Path(locator).is_absolute():
        raise UserConfigError('USER_PROFILE_PATH_INVALID')
    try:
        data = json.loads(Path(locator).read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, ValueError):
        raise UserConfigError('USER_PROFILE_UNREADABLE') from None
    if not isinstance(data, dict) or data.get('schemaVersion') != 1:
        raise UserConfigError('USER_PROFILE_SCHEMA_INVALID')
    repos = data.get('repositories')
    if not isinstance(repos, dict) or any(r not in ROLES for r in required_roles):
        raise UserConfigError('PROFILE_REPOSITORIES_REQUIRED')
    workspace = _path(data.get('workspaceRoot'))
    paths = {role: _path(repos.get(role)) for role in ROLES}
    for path in paths.values():
        if workspace.is_relative_to(path) or path.is_relative_to(workspace):
            raise UserConfigError('WORKSPACE_REPOSITORY_OVERLAP')
    if len(set(paths.values())) != len(ROLES):
        raise UserConfigError('PROFILE_REPOSITORIES_NOT_DISTINCT')
    return {'schemaVersion': 1, 'workspaceRoot': str(workspace),
            'repositories': {role: str(path) for role, path in paths.items()}}


def workspace_root():
    return Path(load_profile()['workspaceRoot'])


def check_write_path(value):
    profile = load_profile()
    path = Path(value).resolve()
    for source in profile['repositories'].values():
        if path.is_relative_to(Path(source)):
            raise UserConfigError('FORMAL_REPOSITORY_READ_ONLY')
    if not path.is_relative_to(Path(profile['workspaceRoot'])):
        raise UserConfigError('WRITE_OUTSIDE_WORKSPACE')
    return path


def main():
    parser = argparse.ArgumentParser(description='Validate K1_REVIEW_PROFILE without printing private values')
    parser.add_argument('command', choices=['check'])
    parser.parse_args()
    try:
        load_profile()
        print(json.dumps({'status': 'ok', 'configuredRoles': list(ROLES)}))
        return 0
    except UserConfigError as error:
        print(json.dumps({'error': str(error)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
