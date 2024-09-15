#!/usr/bin/env python

import glob
import importlib
import os
import inspect
import fnmatch
import re
import sys
from tempfile import mkdtemp
import traceback
import random
import subprocess
from concurrent.futures import ThreadPoolExecutor

from lib import BaseTest
from s3_lib import S3Test
from swift_lib import SwiftTest
from azure_lib import AzureTest
from api_lib import APITest
from fs_endpoint_lib import FileSystemEndpointTest
from testout import TestOut

try:
    from termcolor import colored
except ImportError:
    def colored(s, **kwargs):
        return s


PYTHON_MINIMUM_VERSION = (3, 9)

class Suite():

    def __init__(self, suite, filters, include_long_tests=False, coverage_dir=None, capture_results=None):
        self.suite = suite
        self.orig_stdout = sys.stdout
        self.orig_stderr = sys.stderr
        self.testout = TestOut()
        sys.stdout = self.testout
        sys.stderr = self.testout
        self.lastBase = None
        self.numTests = 0
        self.numFailed = 0
        self.numSkipped = 0
        self.fails = []

        self.tests = []
        self.testignore = []
        for fname in sorted(glob.glob(self.suite + "/*.py"), key=natural_key):
            fname = os.path.splitext(os.path.basename(fname))[0]
            try:
                module = importlib.import_module(suite + "." + fname)
            except Exception as exc:
                self.orig_stdout.write(f"error importing: {suite + '.' + fname}: {exc}\n")
                continue

            if hasattr(module, "TEST_IGNORE"):
                self.testignore = module.TEST_IGNORE
            runnables = []
            for name in sorted(dir(module), key=natural_key):
                if name in self.testignore:
                    continue
                self.testout.clear()

                o = getattr(module, name)

                if not (inspect.isclass(o) and issubclass(o, BaseTest) and o is not BaseTest and
                        o is not SwiftTest and o is not S3Test and o is not AzureTest and
                        o is not APITest and o is not FileSystemEndpointTest):
                    continue

                newBase = o.__bases__[0]
                if self.lastBase is not None and self.lastBase is not newBase:
                    self.lastBase.shutdown_class()

                self.lastBase = newBase

                if filters:
                    matches = False

                    for filt in filters:
                        if fnmatch.fnmatch(o.__name__, filt):
                            matches = True
                            break

                    if not matches:
                        continue

                t = o()

                if t.longTest and not self.include_long_tests or not t.fixture_available() or t.skipTest:
                    self.numSkipped += 1
                    msg = 'SKIP'
                    if t.skipTest and t.skipTest is not True:
                        # If we have a reason to skip, print it
                        msg += ': ' + t.skipTest
                    self.orig_stdout.write("%s: %s ... " % (self.suite, colored(o.__name__, color="yellow", attrs=["bold"])))
                    self.orig_stdout.write(colored(msg + "\n", color="yellow"))
                    self.orig_stdout.flush()
                    continue
                self.numTests += 1

                t.captureResults = capture_results
                t.coverage_dir = coverage_dir
                t.name = o.__name__
                self.tests.append(t)

    def run(self):
        with ThreadPoolExecutor(max_workers=4) as exe:
            exe.map(self.run_test, self.tests)
        if self.lastBase is not None:
            self.lastBase.shutdown_class()

    def run_test(self, t=None, name=None):
        try:
            t.test()
        except Exception as e:
            self.orig_stdout.write(e)
            self.orig_stdout.flush()
            self.numFailed += 1
            typ, val, tb = sys.exc_info()
            self.fails.append((self.suite, t, typ, val, tb, testModule))
            msg = colored("\b\b\b\bFAIL\n", color="red", attrs=["bold"])
            msg += self.testout.get_contents()
            traceback.print_exception(typ, val, tb, file=self.orig_stdout)
        else:
            msg = colored("\b\b\b\bOK \n", color="green", attrs=["bold"])

        self.orig_stdout.write("%s: %s ... " % (self.suite, colored(t.name, color="yellow", attrs=["bold"])))
        self.orig_stdout.write(msg)
        self.orig_stdout.flush()
        t.shutdown()

def natural_key(string_):
    """See https://blog.codinghorror.com/sorting-for-humans-natural-sort-order/"""
    return [int(s) if s.isdigit() else s for s in re.split(r'(\d+)', string_)]


def run(include_long_tests=False, capture_results=False, tests=None, filters=None, coverage_dir=None):
    """
    Run system test.
    """
    print(colored("\n Aptly System Tests\n====================\n", color="green", attrs=["bold"]))

    if not tests:
        tests = sorted(glob.glob("t*_*"), key=natural_key)
    if not coverage_dir:
        coverage_dir = mkdtemp(suffix="aptly-coverage")

    for test in tests:
        try:
            orig_stdout = sys.stdout
            s = Suite(test, filters, include_long_tests, coverage_dir, capture_results)
            s.run()
        except Exception as e:
            sys.stdout = orig_stdout
            print(e)
            traceback.print_exception(e)
        finally:
            sys.stdout = orig_stdout


    print("\nCOVERAGE_RESULTS: %s" % coverage_dir)

    print(f"TESTS: {numTests}    ",
          colored(f"SUCCESS: {numTests - numFailed}    ", color="green", attrs=["bold"]) if numFailed == 0 else
          f"SUCCESS: {numTests - numFailed}    ",
          colored(f"FAIL: {numFailed}    ", color="red", attrs=["bold"]) if numFailed > 0 else "FAIL: 0    ",
          colored(f"SKIP: {numSkipped}", color="yellow", attrs=["bold"]) if numSkipped > 0 else "SKIP: 0")
    print()

    if len(fails) > 0:
        print(colored("FAILURES (%d):" % (len(fails), ), color="red", attrs=["bold"]))

        for (test, t, typ, val, tb, testModule) in fails:
            doc = t.__doc__ or ''
            print(" - %s: %s %s" % (test, colored(t.__class__.__name__, color="yellow", attrs=["bold"]),
                                    testModule.__name__ + ": " + doc.strip()))
        print()
        sys.exit(1)


if __name__ == "__main__":
    if 'APTLY_VERSION' not in os.environ:
        try:
            os.environ['APTLY_VERSION'] = os.popen(
                "make version").read().strip()
        except BaseException as e:
            print("Failed to capture current version: ", e)

    if sys.version_info < PYTHON_MINIMUM_VERSION:
        raise RuntimeError(f'Tests require Python {PYTHON_MINIMUM_VERSION} or higher.')

    output = subprocess.check_output(['gpg', '--version'], text=True)
    if not output.startswith('gpg (GnuPG) 2'):
        raise RuntimeError('Tests require gpg v2')

    output = subprocess.check_output(['gpgv', '--version'], text=True)
    if not output.startswith('gpgv (GnuPG) 2'):
        raise RuntimeError('Tests require gpgv v2')

    os.chdir(os.path.realpath(os.path.dirname(sys.argv[0])))
    random.seed()
    include_long_tests = False
    capture_results = False
    coverage_dir = None
    tests = None
    args = sys.argv[1:]

    while len(args) > 0 and args[0].startswith("--"):
        if args[0] == "--long":
            include_long_tests = True
        elif args[0] == "--capture":
            capture_results = True
        elif args[0] == "--coverage-dir":
            coverage_dir = args[1]
            args = args[1:]

        args = args[1:]

    tests = []
    filters = []

    for arg in args:
        if arg.startswith('t'):
            tests.append(arg)
        else:
            filters.append(arg)

    run(include_long_tests, capture_results, tests, filters, coverage_dir)
