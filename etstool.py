#
#  Copyright (c) 2018, Enthought, Inc.
#  All rights reserved.
#
#  This software is provided without warranty under the terms of the BSD
#  license included in enthought/LICENSE.txt and may be redistributed only
#  under the conditions described in the aforementioned license.  The license
#  is also available online at http://www.enthought.com/licenses/BSD.txt
#
#  Thanks for using Enthought open source!
#
"""
Tasks for development
=====================

This file is intended to be used with a python environment with the
click library to automate the process of setting up test environments
and running the test within them.  This improves repeatability and
reliability of tests be removing many of the variables around the
developer's particular Python environment.  Test environment setup and
package management is performed using `EDM
http://docs.enthought.com/edm/`_

To use this to run your tests, you will need to install EDM and click
into your working environment.  You will also need to have git
installed to access required source code from github repositories.
You can then do::

    python etstool.py install --runtime=... --toolkit=...

to create a test environment from the current codebase and::

    python etstool.py test --runtime=... --toolkit=...

to run tests in that environment.  You can remove the environment with::

    python etstool.py cleanup --runtime=... --toolkit=...

If you make changes you will either need to remove and re-install the
environment or manually update the environment using ``edm``, as
the install performs a ``python setup.py install`` rather than a ``develop``,
so changes in your code will not be automatically mirrored in the test
environment.  You can update with a command like::

    edm run --environment ... -- python setup.py install

You can run all three tasks at once with::

    python etstool.py test_clean --runtime=... --toolkit=...

Currently supported runtime values are ``2.7``, and currently
supported toolkits are ``pyqt``, ``pyside``.

Tests can still be run via the usual means in other environments if that suits
a developer's purpose.

Changing This File
------------------

To change the packages installed during a test run, change the dependencies
variable below.  To install a package from github, or one which is not yet
available via EDM, add it to the `ci-src-requirements.txt` file (these will be
installed by `pip`).

Other changes to commands should be a straightforward change to the listed
commands for each task. See the EDM documentation for more information about
how to run commands within an EDM enviornment.

"""

import glob
import os
import shlex
import subprocess
import sys
from timeit import default_timer
from shutil import rmtree, copy as copyfile
from tempfile import mkdtemp
from contextlib import contextmanager

import click


# core dependencies that will be installed with edm
dependencies = {
    "apptools",
    "enable",
    "pyopengl",
    "six",
    "mayavi",
    "vtk"}

# test dependencies that will be installed with edm
test_dependencies = {
    "cython",
    "nose",
    "coverage",
    "wheel"}

# toolkit dependencies that will be installed with edm
toolkits = {
    'pyside': {'pyside'},
    'pyqt4': {'pyqt<4.12'},  # FIXME: build of 4.12-1 appears to be bad
}

# latest dependencies that can be retrieved from pypi.
pypi_dependencies = {
    "traits",
    "traitsui",
    "pyopengl",
    "mayavi"}

# cutting edge dependencies that can be retrieved from source repositories.
repo_dependencies = {
    "git+https://github.com/nucleic/enaml.git#egg=enaml",
    "git+https://github.com/enthought/traits.git#egg=traits",
    "git+https://github.com/enthought/traitsui.git#egg=traitsui",
    "git+https://github.com/mcfletch/pyopengl.git#egg=pyopengl",
    "git+https://github.com/nucleic/atom.git#egg=atom",
    "git+https://github.com/enthought/mayavi.git#egg=mayavi"}

environment_vars = {
    'pyside': {'ETS_TOOLKIT': 'qt4', 'QT_API': 'pyside'},
    'pyqt': {'ETS_TOOLKIT': 'qt4', 'QT_API': 'pyqt'}}


@click.group()
def cli():
    pass


@cli.command()
@click.option('--runtime', default='2.7', help='The python runtime version')
@click.option(
    '--toolkit', type=click.Choice(['pyside', 'pyqt4']),
    default='pyside', help='The gui toolkit to use')
@click.option(
    '--environment', default=None,
    help='Override the default environment name')
@click.option(
    '--enaml', default='latest',
    help='The enaml version to build against')
@click.option(
    '--source', type=click.Choice(['edm', 'pypi', 'github']),
    default='edm', help='The package source to use')
def install(runtime, toolkit, environment, enaml, source):
    """ Install project and dependencies into a clean EDM environment.

    """
    parameters = get_parameters(runtime, toolkit, environment, enaml, source)
    if source == 'edm':
        # We should not have dependencies installed by pip when
        # source is edm.
        assert len(parameters['pip_packages']) == 0
    # edm commands to setup the development environment
    commands = [
        "edm environments create {environment} --force --version={runtime}",
        "edm install -y -e {environment} {test_packages} {edm_packages}",
        "edm run -e {environment} -- pip install ."
    ]
    if len(parameters['pip_packages']) > 0:
        commands.insert(
            2, "edm run -e {environment} -- pip install -U {pip_packages}")

    click.echo("Creating environment '{environment}'".format(**parameters))
    execute(commands, parameters)
    click.echo('Done install')


@cli.command()
@click.option('--runtime', default='2.7', help='The python runtime version')
@click.option(
    '--toolkit', type=click.Choice(['pyside', 'pyqt4']),
    default='pyside', help='The gui toolkit to use')
@click.option(
    '--environment', default=None,
    help='Override the default environment name')
def test(runtime, toolkit, environment):
    """ Run the test suite in a given environment with the specified toolkit.

    """
    parameters = get_parameters(runtime, toolkit, environment)
    environ = environment_vars.get(toolkit, {}).copy()
    environ['PYTHONUNBUFFERED'] = "1"
    commands = [
        "edm run -e {environment} -- coverage run -p -m nose.core -v traits_enaml --nologcapture"  # noqa
    ]

    # We run in a tempdir to avoid accidentally picking up wrong
    # traits_enaml code from a local dir. We need to ensure a good
    # .coveragerc is in that directory, plus coverage has a bug that
    # means a non-local coverage file doesn't get populated correctly.
    click.echo("Running tests in '{environment}'".format(**parameters))
    with do_in_tempdir(files=['.coveragerc'], capture_files=['./.coverage*']):
        os.environ.update(environ)
        execute(commands, parameters)
    click.echo('Done test')


@cli.command()
@click.option('--runtime', default='2.7', help='The python runtime version')
@click.option(
    '--toolkit', type=click.Choice(['pyside', 'pyqt4']),
    default='pyside', help='The gui toolkit to use')
@click.option(
    '--environment', default=None,
    help='Override the default environment name')
def cleanup(runtime, toolkit, environment):
    """ Remove a development environment.

    """
    parameters = get_parameters(runtime, toolkit, environment)
    commands = [
        "edm run -e {environment} -- python setup.py clean",
        "edm environments remove {environment} --purge -y"
    ]
    click.echo("Cleaning up environment '{environment}'".format(**parameters))
    execute(commands, parameters)
    click.echo('Done cleanup')


@cli.command()
@click.option('--runtime', default='2.7', help='The python runtime version')
@click.option(
    '--toolkit', type=click.Choice(['pyside', 'pyqt4']),
    default='pyside', help='The gui toolkit to use')
@click.option(
    '--environment', default=None,
    help='Override the default environment name')
def test_clean(runtime, toolkit):
    """ Run tests in a clean environment, cleaning up afterwards

    """
    args = ['--toolkit={}'.format(toolkit), '--runtime={}'.format(runtime)]
    try:
        install(args=args, standalone_mode=False)
        test(args=args, standalone_mode=False)
    finally:
        cleanup(args=args, standalone_mode=False)


@cli.command()
@click.option('--runtime', default='2.7', help='The python runtime version')
@click.option(
    '--toolkit', type=click.Choice(['pyside', 'pyqt4']),
    default='pyside', help='The gui toolkit to use')
@click.option(
    '--environment', default=None,
    help='Override the default environment name')
def update(runtime, toolkit, environment):
    """ Update/Reinstall package into environment.

    """
    parameters = get_parameters(runtime, toolkit, environment)
    commands = [
        "edm run -e {environment} -- pip install ."
    ]
    click.echo("Re-installing in  '{environment}'".format(**parameters))
    execute(commands, parameters)
    click.echo('Done update')


# ----------------------------------------------------------------------------
# Utility routines
# ----------------------------------------------------------------------------


def get_parameters(
        runtime, toolkit, environment, version='latest', source='edm'):
    """ Set up parameters dictionary for format() substitution """
    edm_packages = (
        test_dependencies | dependencies | toolkits.get(toolkit, set()))

    if source == 'edm':
        pip_packages = set()
        if version == 'latest':
            edm_packages.add('enaml')
        else:
            edm_packages.add('enaml^={}'.format(version))
    elif source == 'pypi':
        pip_packages = pypi_dependencies
        if version == 'latest':
            pip_packages.add('enaml')
        else:
            pip_packages.add('enaml=={}'.format(version))
    elif source == 'github':
        pip_packages = repo_dependencies
    else:
        raise ValueError(
            'Invalid value for the package source: {}'.format(source))

    if environment is None:
        environment = 'traits-enaml-{runtime}-{toolkit}'.format(
            runtime=runtime, toolkit=toolkit)
    return {
        'runtime': runtime,
        'test_packages': ' '.join(test_dependencies),
        'edm_packages': ' '.join(edm_packages),
        'pip_packages': ' '.join(pip_packages),
        'environment': environment}


@contextmanager
def do_in_tempdir(files=(), capture_files=()):
    """ Create a temporary directory, cleaning up after done.

    Creates the temporary directory, and changes into it.  On exit returns to
    original directory and removes temporary dir.

    Parameters
    ----------
    files : sequence of filenames
        Files to be copied across to temporary directory.
    capture_files : sequence of filenames
        Files to be copied back from temporary directory.
    """
    path = mkdtemp()
    old_path = os.getcwd()

    # send across any files we need
    for filepath in files:
        click.echo('copying file to tempdir: {}'.format(filepath))
        copyfile(filepath, path)

    os.chdir(path)
    try:
        yield path
        # retrieve any result files we want
        for pattern in capture_files:
            for filepath in glob.iglob(pattern):
                click.echo('copying file back: {}'.format(filepath))
                copyfile(filepath, old_path)
    finally:
        os.chdir(old_path)
        rmtree(path)


def execute(commands, parameters):
    for command in commands:
        start = default_timer()
        arguments = command.format(**parameters)
        click.echo("[EXECUTING] {}".format(arguments))
        try:
            subprocess.check_call(shlex.split(arguments))
        except subprocess.CalledProcessError:
            sys.exit(1)
        finally:
            duration = default_timer() - start
            click.echo("[DURATION] {:.2f} s".format(duration))


if __name__ == '__main__':
    cli()
