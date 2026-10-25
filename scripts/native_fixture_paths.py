"""Anchor hosted fixture paths to this loaded helper's verified checkout layout."""
import os
from pathlib import Path
import stat


def checkout_temp(helper):
    # Default actions/checkout on both admitted images is <work>/agent/agent.
    # Derive from code location, never RUNNER_TEMP, argv or the working directory.
    checkout=helper.parent.parent
    if (not helper.is_absolute() or helper.name!='native_fixture_paths.py' or
            helper.parent.name!='scripts' or checkout.name!='agent' or
            checkout.parent.name!='agent'):
        raise ValueError('Expected agent checkout helper layout')
    return checkout.parent.parent/'_temp'


def runner_output():
    helper=Path(__file__)
    directory=checkout_temp(helper)
    info=helper.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or helper.resolve(strict=True)!=helper:
        raise ValueError('Linked or nonregular loaded helper')
    # Only compare external strings; neither selects an I/O or executable path.
    if os.environ.get('RUNNER_TEMP')!=str(directory):
        raise ValueError('Runner temporary directory does not match checkout')
    if directory.resolve(strict=True)!=directory or not directory.is_dir():
        raise ValueError('Linked or missing hosted temporary directory')
    return directory/'vmpower-native'


def require_output(out):
    expected=runner_output()
    if Path(out)!=expected:raise ValueError('Fixed runner fixture directory required')
    return expected
