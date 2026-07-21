# Getting started with Python virtual environments

This tutorial shows you how to create and use a Python virtual environment.
By the end, you can install packages for one project without affecting any
other project on your machine.

## Prerequisites

- Python 3.9 or later installed
- A terminal on macOS or Linux
- Basic familiarity with shell commands

## Why isolation matters

Every Python installation has one global set of packages. When two projects
need different versions of the same library, a global install breaks one of
them. A virtual environment gives each project its own private set of
packages. The `venv` module ships with the Python 3 standard library, so you
don't need to install anything.

## Creating the environment

Create an environment in a directory named `.venv` inside your project:

```bash
python3 -m venv .venv
```

This command creates the directory and copies a private Python interpreter
into it. Run `ls .venv/bin` to see the interpreter and the activation script.

## Activating and installing packages

Activate the environment:

```bash
source .venv/bin/activate
```

Your shell prompt now shows the environment name. Any package you install
goes into `.venv`, not into the system Python:

```bash
pip install requests
pip list
```

The `pip list` output confirms that `requests` is installed in the
environment.

## Deactivating and cleanup

Return to the system Python:

```bash
deactivate
```

To remove the environment entirely, delete its directory. Your project code
is untouched:

```bash
rm -rf .venv
```

## Summary

You created an isolated environment with `python3 -m venv .venv`, activated
it with `source .venv/bin/activate`, installed a package with pip, and
returned to the system Python with `deactivate`. Each project can now manage
its own dependencies.
