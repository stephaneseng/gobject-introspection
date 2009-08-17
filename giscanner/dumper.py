# -*- Mode: Python -*-
# GObject-Introspection - a framework for introspecting GObject libraries
# Copyright (C) 2008 Colin Walters
# Copyright (C) 2008 Johan Dahlin
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place - Suite 330,
# Boston, MA 02111-1307, USA.
#

import os
import subprocess
import tempfile

from .glibtransformer import IntrospectionBinary

# bugzilla.gnome.org/558436
# Compile a binary program which is then linked to a library
# we want to introspect, in order to call its get_type functions.

_PROGRAM_TEMPLATE = '''/* This file is generated, do not edit */
#include <glib.h>
#include <girepository.h>
#include <string.h>

static GOptionEntry entries[] =
{
  { NULL }
};

int
main(int argc, char **argv)
{
  GOptionContext *context;
  GError *error = NULL;

  g_type_init ();
  g_thread_init (NULL);

  context = g_option_context_new ("");
  g_option_context_add_main_entries (context, entries, "girepository-1.0");
  g_option_context_add_group (context, g_irepository_get_option_group ());
  if (!g_option_context_parse (context, &argc, &argv, &error))
    {
      g_printerr ("introspect failed (%d,%d): %s\\n",
                  error->domain, error->code,
                  error->message);
      return 1;
    }
  return 0;
}
'''


class CompilerError(Exception):
    pass


class LinkerError(Exception):
    pass


class DumpCompiler(object):

    def __init__(self, options, get_type_functions):
        self._options = options
        self._get_type_functions = get_type_functions
        # We have to use the current directory to work around Unix
        # sysadmins who mount /tmp noexec
        self._tmpdir = tempfile.mkdtemp('', 'tmp-introspect', dir=os.getcwd())

        self._compiler_cmd = os.environ.get('CC', 'gcc')
        self._linker_cmd = os.environ.get('LD', self._compiler_cmd)
        self._pkgconfig_cmd = os.environ.get('PKG_CONFIG', 'pkg-config')

        self._uninst_srcdir = os.environ.get(
            'UNINSTALLED_INTROSPECTION_SRCDIR')
        self._packages = ['gio-2.0 gthread-2.0']
        if not self._uninst_srcdir:
            self._packages.append('gobject-introspection-1.0')

    # Public API

    def run(self):
        print '  GEN   ' + (self._options.output and
                            self._options.output or '<stdout>')
        c_path = self._generate_tempfile('.c')
        f = open(c_path, 'w')
        f.write(_PROGRAM_TEMPLATE)

        # We need to reference our get_type functions to make sure they are
        # pulled in at the linking stage if the library is a static library
        # rather than a shared library.
        if len(self._get_type_functions) > 0:
            for func in self._get_type_functions:
                f.write("extern GType " + func + "(void);\n")
            f.write("GType (*GI_GET_TYPE_FUNCS_[])(void) = {\n")
            first = True
            for func in self._get_type_functions:
                if first:
                    first = False
                else:
                    f.write(",\n")
                f.write("  " + func)
            f.write("\n};\n")
        f.close()

        o_path = self._generate_tempfile('.o')
        bin_path = self._generate_tempfile()

        try:
            self._compile(o_path, c_path)
        except CompilerError, e:
            raise SystemExit('ERROR: ' + str(e))

        try:
            self._link(bin_path, o_path)
        except LinkerError, e:
            raise SystemExit('ERROR: ' + str(e))

        os.unlink(c_path)

        return IntrospectionBinary([bin_path], self._tmpdir)

    # Private API

    def _generate_tempfile(self, suffix=''):
        tmpl = '%s-%s%s' % (self._options.namespace_name,
                            self._options.namespace_version, suffix)
        return os.path.join(self._tmpdir, tmpl)

    def _run_pkgconfig(self, flag):
        proc = subprocess.Popen(
            [self._pkgconfig_cmd, flag] + self._packages,
            stdout=subprocess.PIPE)
        return proc.communicate()[0].split()

    def _use_libtool_infection(self):
        libtool_infection = not self._options.nolibtool
        if not libtool_infection:
            return None

        libtool_path = self._options.libtool_path
        if libtool_path:
            # Automake by default sets:
            # LIBTOOL = $(SHELL) $(top_builddir)/libtool
            # To be strictly correct we would have to parse shell.  For now
            # we simply split().
            return libtool_path.split(' ')

        try:
            subprocess.check_call(['libtool', '--version'])
        except subprocess.CalledProcessError, e:
            # If libtool's not installed, assume we don't need it
            return None

        return ['libtool']

    def _compile(self, output, *sources):
        # Not strictly speaking correct, but easier than parsing shell
        args = self._compiler_cmd.split()
        if self._compiler_cmd == 'gcc':
            args.append('-Wall')
        pkgconfig_flags = self._run_pkgconfig('--cflags')
        if self._uninst_srcdir:
            args.append('-I' + os.path.join(self._uninst_srcdir,
                                               'girepository'))
        args.extend(pkgconfig_flags)
        for include in self._options.cpp_includes:
            args.append('-I' + include)
        args.extend(['-c', '-o', output])
        for source in sources:
            if not os.path.exists(source):
                raise CompilerError(
                    "Could not find c source file: %s" % (source, ))
        args.extend(list(sources))
        subprocess.check_call(args)

    def _link(self, output, *sources):
        args = []
        libtool = self._use_libtool_infection()
        if libtool:
            args.extend(libtool)
            args.append('--mode=link')
            args.append('--tag=CC')
            args.append('--silent')

        args.extend([self._linker_cmd, '-o', output])

        # Make sure to list the library to be introspected first since it's
        # likely to be uninstalled yet and we want the uninstalled RPATHs have
        # priority (or we might run with installed library that is older)

        # Search the current directory first
        args.append('-L.')

        uninst_builddir = os.environ.get('UNINSTALLED_INTROSPECTION_BUILDDIR')
        # hack for building GIRepository.gir, skip -lgirepository-1.0 since
        # libgirepository-1.0.la is not in current directory and we refer to it
        # explicitly below anyway
        for library in self._options.libraries:
            if (uninst_builddir and
                self._options.libraries[0] == 'girepository-1.0'):
                continue
            args.append('-l' + library)

        # hack for building gobject-introspection itself
        if uninst_builddir:
            path = os.path.join(uninst_builddir, 'girepository',
                                'libgirepository-1.0.la')
            args.append(path)

        args.extend(self._run_pkgconfig('--libs'))
        for source in sources:
            if not os.path.exists(source):
                raise CompilerError(
                    "Could not find object file: %s" % (source, ))
        args.extend(list(sources))

        subprocess.check_call(args)


def compile_introspection_binary(options, get_type_functions):
    dc = DumpCompiler(options, get_type_functions)
    return dc.run()
