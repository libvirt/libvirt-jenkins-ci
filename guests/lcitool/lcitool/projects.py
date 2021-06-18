# projects.py - module containing per-project package mapping primitives
#
# Copyright (C) 2017-2020 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import logging
import yaml

from pathlib import Path
from pkg_resources import resource_filename

from lcitool import util
from lcitool.singleton import Singleton

log = logging.getLogger(__name__)


class Projects(metaclass=Singleton):

    def __init__(self):
        mappings_path = resource_filename(__name__,
                                          "ansible/vars/mappings.yml")

        try:
            with open(mappings_path, "r") as infile:
                mappings = yaml.safe_load(infile)
                self._mappings = mappings["mappings"]
                self._pypi_mappings = mappings["pypi_mappings"]
                self._cpan_mappings = mappings["cpan_mappings"]
        except Exception as ex:
            raise Exception(f"Can't load mappings: {ex}")

        source = Path(resource_filename(__name__, "ansible/vars/projects"))

        self._packages = {}
        for item in source.iterdir():
            yaml_path = Path(source, item)
            if not yaml_path.is_file():
                continue
            if yaml_path.suffix != ".yml":
                continue

            project = item.stem

            log.debug(f"Loading mappings for project '{project}'")
            try:
                with open(yaml_path, "r") as infile:
                    packages = yaml.safe_load(infile)
                    self._packages[project] = packages["packages"]
            except Exception as ex:
                raise Exception(f"Can't load packages for '{project}': {ex}")

    def expand_pattern(self, pattern):
        try:
            projects_expanded = util.expand_pattern(pattern, self._packages,
                                                    "project")
        except Exception as ex:
            raise Exception(f"Failed to expand '{pattern}': {ex}")

        # Some projects are internal implementation details and should
        # not be exposed to the user
        internal_projects = {
            "base",
            "cloud-init",
            "developer",
            "perl-cpan",
            "python-pip",
            "unwanted",
            "vm",
        }

        return list(projects_expanded - internal_projects)

    def get_mappings(self):
        return self._mappings

    def get_pypi_mappings(self):
        return self._pypi_mappings

    def get_cpan_mappings(self):
        return self._cpan_mappings

    def get_packages(self, project):
        return self._packages[project]
