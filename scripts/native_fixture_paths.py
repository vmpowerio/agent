"""Validate the hosted runner's temporary directory before touching its paths."""
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re


def validate_runner_temp(value, windows=False):
    # The admitted images use absolute paths ending in _temp. Accept other
    # ordinary directory names/drive letters without allowing traversal, URI,
    # device/UNC paths, control characters or ambiguous normalized aliases.
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_:/\\ -]+',value):
        raise ValueError('Invalid hosted runner temporary directory')
    parsed=(PureWindowsPath if windows else PurePosixPath)(value)
    if not parsed.is_absolute() or parsed.name!='_temp' or str(parsed)!=value:
        raise ValueError('Absolute canonical hosted temporary directory required')
    if windows and not re.fullmatch(r'[A-Za-z]:',parsed.drive):
        raise ValueError('Local hosted runner drive required')
    if not windows and ('\\' in value or ':' in value or value.startswith('//')):
        raise ValueError('Local hosted runner path required')
    return value


def runner_output():
    raw=validate_runner_temp(os.environ.get('RUNNER_TEMP'),os.name=='nt')
    directory=Path(raw)
    if directory.resolve(strict=True)!=directory or not directory.is_dir():
        raise ValueError('Linked or missing hosted temporary directory')
    return directory/'vmpower-native'


def require_output(out):
    expected=runner_output()
    if Path(out)!=expected:raise ValueError('Fixed runner fixture directory required')
    return expected
