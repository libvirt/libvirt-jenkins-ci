# formatters.py - module containing various recipe formatting backends
#
# Copyright (C) 2017-2020 Red Hat, Inc.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import abc
import logging

from pkg_resources import resource_filename

from lcitool import util
from lcitool.inventory import Inventory
from lcitool.projects import Projects

log = logging.getLogger(__name__)


class FormatterError(Exception):
    """
    Global exception type for this module.

    Contains a detailed message coming from one of its subclassed exception
    types. On the application level, this is the exception type you should be
    catching instead of the subclassed types.
    """

    pass


class DockerfileError(FormatterError):
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return f"Docker formatter error: {self.message}"


class VariablesError(FormatterError):
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return f"Variables formatter error: {self.message}"


class Formatter(metaclass=abc.ABCMeta):
    """
    This an abstract base class that each formatter must subclass.
    """

    @abc.abstractmethod
    def format(self):
        """
        Outputs a recipe using format implemented by a Foo(Formatter) subclass

        Given the input, this method will generate and output an instruction
        recipe (or a configuration file in general) using the format of the
        subclassed formatter. Each formatter must implement this method.

        Returns a formatted recipe as string.
        """
        pass

    def _get_meson_cross(self, cross_abi):
        cross_name = resource_filename(__name__,
                                       f"cross/{cross_abi}.meson")
        with open(cross_name, "r") as c:
            return c.read().rstrip()

    def _generator_build_varmap(self,
                                facts,
                                mappings,
                                pypi_mappings,
                                cpan_mappings,
                                selected_projects,
                                cross_arch):
        projects = Projects()
        pkgs = {}
        cross_pkgs = {}
        pypi_pkgs = {}
        cpan_pkgs = {}
        base_keys = [
            "default",
            facts["packaging"]["format"],
            facts["os"]["name"],
            facts["os"]["name"] + facts["os"]["version"],
        ]
        cross_keys = []
        cross_policy_keys = []
        native_arch = util.get_native_arch()
        native_keys = base_keys + [native_arch + "-" + k for k in base_keys]

        if cross_arch:
            cross_keys = ["cross-" + cross_arch + "-" + k for k in base_keys]
            cross_policy_keys = ["cross-policy-" + k for k in base_keys]

            if facts["packaging"]["format"] == "deb":
                # For Debian-based distros, the name of the foreign package
                # is usually the same as the native package, but there might
                # be architecture-specific overrides, so we have to look both
                # at the neutral keys and at the specific ones
                arch_keys = [cross_arch + "-" + k for k in base_keys]
                cross_keys = base_keys + arch_keys + cross_keys

        # We need to add the base project manually here: the standard
        # machinery hides it because it's an implementation detail
        for project in selected_projects + ["base"]:
            for package in projects.get_packages(project):
                cross_policy = "native"

                if (package not in mappings and
                    package not in pypi_mappings and
                    package not in cpan_mappings):
                    raise Exception(f"No mapping defined for {package}")

                if package in mappings:
                    for key in cross_policy_keys:
                        if key in mappings[package]:
                            cross_policy = mappings[package][key]

                    if cross_policy not in ["native", "foreign", "skip"]:
                        raise Exception(
                            f"Unexpected cross arch policy {cross_policy} for "
                            f"{package}"
                        )

                    if cross_arch and cross_policy == "foreign":
                        for key in cross_keys:
                            if key in mappings[package]:
                                pkgs[package] = mappings[package][key]
                    else:
                        for key in native_keys:
                            if key in mappings[package]:
                                pkgs[package] = mappings[package][key]

                if package in pypi_mappings:
                    if "default" in pypi_mappings[package]:
                        pypi_pkgs[package] = pypi_mappings[package]["default"]

                if package in cpan_mappings:
                    if "default" in cpan_mappings[package]:
                        cpan_pkgs[package] = cpan_mappings[package]["default"]

                if package in pkgs and pkgs[package] is None:
                    del pkgs[package]
                if package in pypi_pkgs and pypi_pkgs[package] is None:
                    del pypi_pkgs[package]
                if package in cpan_pkgs and cpan_pkgs[package] is None:
                    del cpan_pkgs[package]
                if package in pypi_pkgs and package in pkgs:
                    del pypi_pkgs[package]
                if package in cpan_pkgs and package in pkgs:
                    del cpan_pkgs[package]

                if (package not in pkgs and
                    package not in pypi_pkgs and
                    package not in cpan_pkgs):
                    continue

                if package in pkgs and cross_policy == "foreign":
                    cross_pkgs[package] = pkgs[package]
                if package in pkgs and cross_policy in ["skip", "foreign"]:
                    del pkgs[package]

        extra_projects = []
        if pypi_pkgs:
            extra_projects += ["python-pip"]
        if cpan_pkgs:
            extra_projects += ["perl-cpan"]
        for project in extra_projects:
            for package in projects.get_packages(project):
                if package not in mappings:
                    raise Exception(f"No mapping defined for {package}")

                for key in native_keys:
                    if key in mappings[package]:
                        pkgs[package] = mappings[package][key]

                if pkgs[package] is None:
                    raise Exception(f"No package for {package}")

        varmap = {
            "packaging_command": facts["packaging"]["command"],
            "paths_ccache": facts["paths"]["ccache"],
            "paths_make": facts["paths"]["make"],
            "paths_ninja": facts["paths"]["ninja"],
            "paths_python": facts["paths"]["python"],
            "paths_pip3": facts["paths"]["pip3"],
        }

        if cross_arch:
            varmap["cross_arch"] = cross_arch
            varmap["cross_abi"] = util.native_arch_to_abi(cross_arch)

            if facts["packaging"]["format"] == "deb":
                cross_arch_deb = util.native_arch_to_deb_arch(cross_arch)
                varmap["cross_arch_deb"] = cross_arch_deb

                # For Debian-based distros, the name of the foreign package
                # is obtained by appending the foreign architecture (in
                # Debian format) to the name of the native package.
                #
                # The exception to this is cross-compilers, where we
                # have to install the package for the native architecture
                # in order to be able to build for the foreign architecture
                for key in cross_pkgs.keys():
                    if (cross_pkgs[key].startswith("gcc-") or
                        cross_pkgs[key].startswith("g++-")):
                        continue
                    cross_pkgs[key] = cross_pkgs[key] + ":" + cross_arch_deb

        varmap["mappings"] = sorted(set(list(pkgs.keys()) +
                                        list(cross_pkgs.keys()) +
                                        list(pypi_pkgs.keys()) +
                                        list(cpan_pkgs.keys())))

        if pkgs:
            varmap["pkgs"] = sorted(set(pkgs.values()))
        if cross_pkgs:
            varmap["cross_pkgs"] = sorted(set(cross_pkgs.values()))
        if pypi_pkgs:
            varmap["pypi_pkgs"] = sorted(set(pypi_pkgs.values()))
        if cpan_pkgs:
            varmap["cpan_pkgs"] = sorted(set(cpan_pkgs.values()))

        log.debug(f"Generated varmap: {varmap}")
        return varmap

    def _generator_prepare(self, hosts, selected_projects, cross_arch):
        log.debug(f"Generating varmap for "
                  f"hosts='{hosts}', "
                  f"projects='{selected_projects}', "
                  f"cross_arch='{cross_arch}'")

        name = self.__class__.__name__.lower()
        projects = Projects()
        mappings = projects.mappings
        pypi_mappings = projects.pypi_mappings
        cpan_mappings = projects.cpan_mappings
        native_arch = util.get_native_arch()

        if len(hosts) > 1:
            raise FormatterError("Can't use this generator on multiple hosts")
        host = hosts[0]

        facts = Inventory().get_facts(host)

        # We can only generate Dockerfiles for Linux
        if (name == "dockerfileformatter" and
            facts["packaging"]["format"] not in ["apk", "deb", "rpm"]):
            raise FormatterError(f"Host {host} doesn't support this generator")
        if cross_arch:
            osname = facts["os"]["name"]
            if osname not in ["Debian", "Fedora"]:
                raise FormatterError(f"Cannot cross compile on {osname}")
            if (osname == "Debian" and cross_arch.startswith("mingw")):
                raise FormatterError(
                    f"Cannot cross compile for {cross_arch} on {osname}"
                )
            if (osname == "Fedora" and not cross_arch.startswith("mingw")):
                raise FormatterError(
                    f"Cannot cross compile for {cross_arch} on {osname}"
                )
            if cross_arch == native_arch:
                raise FormatterError(
                    f"Cross arch {cross_arch} should differ from native "
                    f"{native_arch}"
                )

        varmap = self._generator_build_varmap(facts,
                                              mappings,
                                              pypi_mappings,
                                              cpan_mappings,
                                              selected_projects,
                                              cross_arch)
        return facts, cross_arch, varmap


class DockerfileFormatter(Formatter):
    def _format_dockerfile(self, host, project, facts, cross_arch, varmap):
        strings = []
        pkg_align = " \\\n" + (" " * len("RUN " + facts["packaging"]["command"] + " "))
        pypi_pkg_align = " \\\n" + (" " * len("RUN pip3 "))
        cpan_pkg_align = " \\\n" + (" " * len("RUN cpanm "))

        varmap["pkgs"] = pkg_align[1:] + pkg_align.join(varmap["pkgs"])

        if "cross_pkgs" in varmap:
            varmap["cross_pkgs"] = pkg_align[1:] + pkg_align.join(varmap["cross_pkgs"])
        if "pypi_pkgs" in varmap:
            varmap["pypi_pkgs"] = pypi_pkg_align[1:] + pypi_pkg_align.join(varmap["pypi_pkgs"])
        if "cpan_pkgs" in varmap:
            varmap["cpan_pkgs"] = cpan_pkg_align[1:] + cpan_pkg_align.join(varmap["cpan_pkgs"])

        base = facts["containers"]["base"]
        strings.append(f"FROM {base}")

        commands = []

        varmap["nosync"] = ""
        if facts["packaging"]["format"] == "apk":
            # TODO: 'libeatmydata' package is present in 'testing' repo
            # for Alpine Edge. Once it graduates to 'main' repo we
            # should use it here
            # varmap["nosync"] = "eatmydata "
            # "{packaging_command} add libeatmydata",
            commands.extend([
                "{packaging_command} update",
                "{packaging_command} upgrade"])

            commands.extend([
                "{nosync}{packaging_command} add {pkgs}",
            ])
        elif facts["packaging"]["format"] == "deb":
            varmap["nosync"] = "eatmydata "
            commands.extend([
                "export DEBIAN_FRONTEND=noninteractive",
                "{packaging_command} update",
                "{packaging_command} install -y eatmydata",
                "{nosync}{packaging_command} dist-upgrade -y"])

            # JDK fails to install in -slim images due to
            # a postin script failing to run update-alternatives.
            # Create the missing dir until a fix is decided for
            # either the tool that does container minimization:
            #   https://github.com/debuerreotype/debuerreotype/issues/10
            # or the JDK package
            #   https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=955619
            if (facts["os"]["name"] == "Debian" and
                ("java" in varmap["mappings"] or
                    "publican" in varmap["mappings"])):
                commands.extend(["mkdir -p /usr/share/man/man1"])

            commands.extend([
                "{nosync}{packaging_command} install --no-install-recommends -y {pkgs}",
                "{nosync}{packaging_command} autoremove -y",
                "{nosync}{packaging_command} autoclean -y",
                "sed -Ei 's,^# (en_US\\.UTF-8 .*)$,\\1,' /etc/locale.gen",
                "dpkg-reconfigure locales",
                "dpkg-query --showformat '${{Package}}_${{Version}}_${{Architecture}}\\n' --show > /packages.txt",
            ])
        elif facts["packaging"]["format"] == "rpm":
            # Rawhide needs this because the keys used to sign packages are
            # cycled from time to time
            if facts["os"]["name"] == "Fedora" and facts["os"]["version"] == "Rawhide":
                commands.extend([
                    "{packaging_command} update -y --nogpgcheck fedora-gpg-keys",
                ])

            if facts["os"]["name"] == "Fedora":
                varmap["nosync"] = "nosync "
                nosyncsh = [
                    "#!/bin/sh",
                    "if test -d /usr/lib64",
                    "then",
                    "    export LD_PRELOAD=/usr/lib64/nosync/nosync.so",
                    "else",
                    "    export LD_PRELOAD=/usr/lib/nosync/nosync.so",
                    "fi",
                    "exec \"$@\""
                ]
                commands.extend([
                    "{packaging_command} install -y nosync",
                    "echo -e '%s' > /usr/bin/nosync" % "\\n\\\n".join(nosyncsh),
                    "chmod +x /usr/bin/nosync"])

            # First we need to run update, then config and install
            commands.extend(["{nosync}{packaging_command} update -y"])

            if facts["os"]["name"] == "CentOS":
                # Starting with CentOS 8, most -devel packages are shipped in
                # the PowerTools repository, which is not enabled by default
                commands.extend([
                    "{nosync}{packaging_command} install 'dnf-command(config-manager)' -y",
                    "{nosync}{packaging_command} config-manager --set-enabled -y powertools",
                ])

                # Not all of the virt related -devel packages are provided by
                # virt:rhel module so we have to enable AV repository as well.
                commands.extend([
                    "{nosync}{packaging_command} install -y centos-release-advanced-virtualization",
                ])

                # Some of the packages we need are not part of CentOS proper
                # and are only available through EPEL
                commands.extend([
                    "{nosync}{packaging_command} install -y epel-release",
                ])

            commands.extend(["{nosync}{packaging_command} install -y {pkgs}"])

            # openSUSE doesn't seem to have a convenient way to remove all
            # unnecessary packages, but CentOS and Fedora do
            if facts["os"]["name"] == "OpenSUSE":
                commands.extend([
                    "{nosync}{packaging_command} clean --all",
                ])
            else:
                commands.extend([
                    "{nosync}{packaging_command} autoremove -y",
                    "{nosync}{packaging_command} clean all -y",
                ])

            commands.extend(["rpm -qa | sort > /packages.txt"])

        if "ccache" in varmap["mappings"]:
            compilers = set()
            for compiler in ["gcc", "clang"]:
                if compiler in varmap["mappings"]:
                    compilers.add(compiler)
                    compilers.add("cc")
            for compiler in ["g++"]:
                if compiler in varmap["mappings"]:
                    compilers.add(compiler)
                    compilers.add("c++")

            if compilers:
                commands.extend([
                    "mkdir -p /usr/libexec/ccache-wrappers",
                ])

                for compiler in sorted(compilers):
                    if cross_arch:
                        compiler = "{cross_abi}-" + compiler
                    commands.extend([
                        "ln -s {paths_ccache} /usr/libexec/ccache-wrappers/" + compiler,
                    ])

        script = "\nRUN " + (" && \\\n    ".join(commands))
        strings.append(script.format(**varmap))

        if cross_arch:
            cross_commands = []

            # Intentionally a separate RUN command from the above
            # so that the common packages of all cross-built images
            # share a Docker image layer.
            if facts["packaging"]["format"] == "deb":
                cross_commands.extend([
                    "export DEBIAN_FRONTEND=noninteractive",
                    "dpkg --add-architecture {cross_arch_deb}",
                    "{nosync}{packaging_command} update",
                    "{nosync}{packaging_command} dist-upgrade -y",
                    "{nosync}{packaging_command} install --no-install-recommends -y dpkg-dev",
                    "{nosync}{packaging_command} install --no-install-recommends -y {cross_pkgs}",
                    "{nosync}{packaging_command} autoremove -y",
                    "{nosync}{packaging_command} autoclean -y",
                ])
            elif facts["packaging"]["format"] == "rpm":
                cross_commands.extend([
                    "{nosync}{packaging_command} install -y {cross_pkgs}",
                    "{nosync}{packaging_command} clean all -y",
                ])

            if not cross_arch.startswith("mingw"):
                cross_commands.extend([
                    "mkdir -p /usr/local/share/meson/cross",
                    "echo \"{cross_meson}\" > /usr/local/share/meson/cross/{cross_abi}",
                ])

                cross_meson = self._get_meson_cross(varmap["cross_abi"])
                varmap["cross_meson"] = cross_meson.replace("\n", "\\n\\\n")

            cross_script = "\nRUN " + (" && \\\n    ".join(cross_commands))
            strings.append(cross_script.format(**varmap))

        if "pypi_pkgs" in varmap:
            strings.append("\nRUN pip3 install {pypi_pkgs}".format(**varmap))

        if "cpan_pkgs" in varmap:
            strings.append("\nRUN cpanm --notest {cpan_pkgs}".format(**varmap))

        common_vars = ["ENV LANG \"en_US.UTF-8\""]
        if "make" in varmap["mappings"]:
            common_vars += ["ENV MAKE \"{paths_make}\""]
        if "meson" in varmap["mappings"]:
            common_vars += ["ENV NINJA \"{paths_ninja}\""]
        if "python3" in varmap["mappings"]:
            common_vars += ["ENV PYTHON \"{paths_python}\""]
        if "ccache" in varmap["mappings"]:
            common_vars += ["ENV CCACHE_WRAPPERSDIR \"/usr/libexec/ccache-wrappers\""]

        common_env = "\n" + "\n".join(common_vars)
        strings.append(common_env.format(**varmap))

        if cross_arch:
            cross_vars = ["ENV ABI \"{cross_abi}\""]
            if "autoconf" in varmap["mappings"]:
                cross_vars.append("ENV CONFIGURE_OPTS \"--host={cross_abi}\"")

            if "meson" in varmap["mappings"]:
                if cross_arch.startswith("mingw"):
                    cross_vars.append(
                        "ENV MESON_OPTS \"--cross-file=/usr/share/mingw/toolchain-{cross_arch}.meson\""
                    )
                else:
                    cross_vars.append(
                        "ENV MESON_OPTS \"--cross-file={cross_abi}\"",
                    )

            cross_env = "\n" + "\n".join(cross_vars)
            strings.append(cross_env.format(**varmap))

        return strings

    def format(self, hosts, selected_projects, cross_arch):
        """
        Generates and formats a Dockerfile.

        Given the application commandline arguments, this function will take
        the projects and inventory attributes and generate a Dockerfile
        describing an environment for doing a project build on a given
        inventory platform.

        :param args: Application class' command line arguments
        :returns: String represented Dockerfile
        """

        log.debug(f"Generating Dockerfile for projects '{selected_projects}' "
                  f"on host '{hosts}' (cross_arch={cross_arch})")

        try:
            facts, cross_arch, varmap = self._generator_prepare(hosts,
                                                                selected_projects,
                                                                cross_arch)
        except FormatterError as ex:
            raise DockerfileError(str(ex))

        return '\n'.join(self._format_dockerfile(hosts, selected_projects,
                                                 facts, cross_arch, varmap))


class VariablesFormatter(Formatter):
    @staticmethod
    def _format_variables(varmap):
        strings = []

        for key in varmap:
            if key == "mappings":
                # For internal use only
                continue
            if key == "pkgs" or key.endswith("_pkgs"):
                name = key
                value = " ".join(varmap[key])
            elif key.startswith("paths_"):
                name = key[len("paths_"):]
                value = varmap[key]
            else:
                name = key
                value = varmap[key]
            uppername = name.upper()
            strings.append(f"{uppername}='{value}'")
        return strings

    def format(self, hosts, selected_projects, cross_arch):
        """
        Generates and formats environment variables as KEY=VAL pairs.

        Given the commandline arguments, this function will take take the
        projects and inventory attributes and generate a KEY=VAL encoded list
        of environment variables that can be consumed by various CI backends.

        :param args: Application class' command line arguments
        :returns: String represented list of environment variables
        """

        log.debug(f"Generating variables for projects '{selected_projects} on "
                  f"host '{hosts}' (cross_arch={cross_arch})")

        try:
            _, _, varmap = self._generator_prepare(hosts,
                                                   selected_projects,
                                                   cross_arch)
        except FormatterError as ex:
            raise VariablesError(str(ex))

        return '\n'.join(self._format_variables(varmap))
