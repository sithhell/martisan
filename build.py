# Copyright (c) Thomas Heller
#
# Distributed under the Boost Software License, Version 1.0. (See accompanying
# file LICENSE_1_0.txt or copy at http://www.boost.org/LICENSE_1_0.txt)
#

'''
build.py is a python script that is used to build and install software for a
modules environment. It looks for json files in the packages directory. The
software is built taking different compiler, MPI, python and Boost versions
into account.
'''

from __future__ import print_function

import argparse
import json
import os
import pty
import urllib2
import subprocess
import bz2
import gzip
import tarfile
import zipfile
import itertools
import shutil

modules = ['Compiler', 'MPI', 'Boost', 'CUDA', 'Python']
architectures = ['x86_64']

packages = {}

package_path = os.path.dirname(os.path.realpath(__file__))
package_path = os.path.join(package_path, 'packages')

def find_package(package):
    for module in packages:
        name = package.split('/', 2)
        if name[0] in packages[module]:
            found = packages[module][name[0]]
            if len(name) == 2:
                version = name[1]
            else:
                version = '*'
            if found.has_version(version):
                return (module, found, version)

    return ('', Package(), '*')

def download_source(url, destination):
    file_name = url.split('/')[-1]
    file_name = os.path.join(destination, file_name)

    if not os.path.exists(file_name):
        u = urllib2.urlopen(url)
        meta = u.info()
        try:
            file_size = int(meta.getheaders('Content-Length')[0])
        except:
            file_size = 0
        print ('Downloading: %s Bytes: %s' % (url, file_size))
        f = open(file_name, 'wb')
        file_size_dl = 0
        block_sz = 8192
        while True:
            buffer = u.read(block_sz)
            if not buffer:
                break

            file_size_dl += len(buffer)
            f.write(buffer)
            if file_size > 0.0:
                status = '\r%10d [%3.2f%%]' % (file_size_dl, file_size_dl * 100. / file_size)
            else:
                status = '\r'
            status = status + chr(8)*(len(status) + 1)
            print (status, end='')
        f.close()
        print ('\rDownload complete: 100%')

    return file_name

def extract_source(source_path, file_name):
    archive = None
    if zipfile.is_zipfile(file_name):
        archive = zipfile.ZipFile(file_name, 'r')
        src_dir = os.path.dirname(archive.namelist()[0])
        if src_dir == '':
            src_dir = os.path.splitext(file_name)[0]

    elif tarfile.is_tarfile(file_name):
        archive = tarfile.open(file_name, 'r')
        src_info = archive.getmembers()[0]
        if src_info.type != tarfile.DIRTYPE:
            src_dir = os.path.splitext(os.path.splitext(file_name)[0])[0]
        else:
            src_dir = src_info.name

    src_dir = os.path.join(source_path, src_dir)

    if not os.path.exists(src_dir):
        archive.extractall(source_path)
        print('Extracted %s to %s' % (file_name, src_dir))

    return src_dir

class Package:
    def __init__(self, json_data = {}):
        self.json_data = json_data

    def __str__(self):
        if 'name' in self.json_data:
            return 'Package ' + self.json_data['name']
        else:
            return 'Invalid Package'

    def __repr__(self):
        return self.__str__()

    def __nonzero__(self):
        return 'name' in self.json_data

    def name(self):
        return self.json_data['name']

    def architectures(self):
        return self.json_data['architecture']

    def supports_arch(self, arch):
        if self.architecture() == 'all':
            return True;
        return self.architecture() == arch;

    def has_version(self, version):
        if version == '*':
            return True
        for v in self.json_data['versions']:
            if v[0] == version:
                return True;

    def versions(self):
        versions = []
        if 'versions' in self.json_data:
            for version in self.json_data['versions']:
                versions.extend([version[0]])

        return versions

    def module_env_vars(self):
        if 'modulefile' in self.json_data:
            modulefile = self.json_data['modulefile']
            if 'env' in modulefile:
                return modulefile['env']
        return {}

    def module_path_vars(self):
        if 'modulefile' in self.json_data:
            modulefile = self.json_data['modulefile']
            if 'paths' in modulefile:
                return modulefile['paths']
        return {}

    def get_data(self, name):
        if name in self.json_data:
            return self.json_data[name]
        return ''

    def resolve_dependencies(self):
        def get_deps(name):
            module_deps = []
            optional_module_deps = []
            deps = []
            optional_deps = []
            if name in self.json_data:
                for dep in self.json_data[name]:
                    optional = False
                    if dep[0] == '+':
                        optional = True
                        dep = dep[1:]

                    if dep in modules:
                        for n in packages[dep]:
                            if optional:
                                optional_module_deps.extend([(dep, packages[dep][n], '*')])
                            else:
                                module_deps.extend([(dep, packages[dep][n], '*')])
                    else:
                        (module, package, version) = find_package(dep)
                        if (package):
                            if optional:
                                optional_deps.extend([(module, package, version)])
                            else:
                                deps.extend([(module, package, version)])

            ddeps = []
            for (omodule, opackage, version) in optional_deps:
                tmp = [(omodule, opackage, version)]
                for (module, package, version) in deps:
                    tmp.extend([(module, package, version)])
                ddeps.extend([tmp])

            res_deps = []

            if len(module_deps) == 0:
                res_deps.extend([deps])
                res_deps.extend(ddeps)
            else:
                for (mmodule, mpackage, mversion) in module_deps:
                    tmp = [(mmodule, mpackage, mversion)]
                    for (module, package, version) in deps:
                        tmp.extend([(module, package, version)])
                    res_deps.extend([tmp])
                    for d in ddeps:
                        tmp = [(mmodule, mpackage, mversion)]
                        for (module, package, version) in d:
                            tmp.extend([(module, package, version)])
                        res_deps.extend([tmp])

            res_deps_opt = []

            for (mmodule, mpackage, mversion) in optional_module_deps:
                for rdeps in res_deps:
                    tmp = [(mmodule, mpackage, mversion)]
                    for (module, package, version) in rdeps:
                        tmp.extend([(module, package, version)])
                    res_deps_opt.extend([tmp])

            return res_deps + res_deps_opt

        self.build_deps = get_deps('dependencies')

    def prefix(self, basepath, arch, version, deps=None):
        path = os.path.join(basepath, arch)
        if deps:
            if 'compiler' in deps:
                path = os.path.join(path,
                    deps['compiler'][0].name() + '-' + deps['compiler'][1][0])
            if 'mpi' in deps:
                path = os.path.join(path,
                    deps['mpi'][0].name() + '-' + deps['mpi'][1][0])
            if 'cuda' in deps:
                path = os.path.join(path,
                    deps['cuda'][0].name() + '-' + deps['cuda'][1][0])
            if 'python' in deps:
                path = os.path.join(path,
                    deps['python'][0].name() + '-' + deps['python'][1][0])
            if 'boost' in deps:
                path = os.path.join(path,
                    deps['boost'][0].name() + '-' + deps['boost'][1][0])

        return os.path.join(path, self.name(), version)

    def build_dir(self, basepath, arch, version, deps=None):
        path = os.path.join(basepath, "build", arch)
        if deps:
            if 'compiler' in deps:
                path = os.path.join(path,
                    deps['compiler'][0].name() + '-' + deps['compiler'][1][0])
            if 'mpi' in deps:
                path = os.path.join(path,
                    deps['mpi'][0].name() + '-' + deps['mpi'][1][0])
            if 'cuda' in deps:
                path = os.path.join(path,
                    deps['cuda'][0].name() + '-' + deps['cuda'][1][0])
            if 'python' in deps:
                path = os.path.join(path,
                    deps['python'][0].name() + '-' + deps['python'][1][0])
            if 'boost' in deps:
                path = os.path.join(path,
                    deps['boost'][0].name() + '-' + deps['boost'][1][0])

        if not os.path.exists(path):
            os.makedirs(path)

        return path

    def get_deps_path(self, deps):
        get_dir = lambda name: '%s-%s' % (deps[name][0].name(), deps[name][1][0]) if name in deps else ''

        deps_dir = ''
        compiler_dir = get_dir('compiler')
        if compiler_dir:
            deps_dir = deps_dir + compiler_dir
        cuda_dir = get_dir('cuda')
        if cuda_dir:
            deps_dir = deps_dir + '-' +  cuda_dir
        mpi_dir = get_dir('mpi')
        if mpi_dir:
            deps_dir = deps_dir + '-' +  mpi_dir
        python_dir = get_dir('python')
        if python_dir:
            deps_dir = deps_dir + '-' +  python_dir
        boost_dir = get_dir('boost')
        if boost_dir:
            deps_dir = deps_dir + '-' +  boost_dir

        return deps_dir

    def base_modulefile(self, basepath, arch, rext_deps, deps):
        modulefiles = os.path.join(basepath, arch, 'modulefiles')

        deps_dir = self.get_deps_path(rext_deps)

        modulefiles = os.path.join(modulefiles, deps_dir)
        prefix_base = os.path.dirname(self.prefix(basepath, arch, 'nan', rext_deps))


        if deps_dir == '':
            base_module_dir = os.path.join(modulefiles, 'Core', '.base', self.name())
        else:
            base_module_dir = os.path.join(modulefiles, '.base', self.name())
        base_module = os.path.join(base_module_dir, 'generic.lua')

        if os.path.exists(base_module):
            if deps_dir == '':
                return (base_module, os.path.join(modulefiles, 'Core', self.name()))
            else:
                return (base_module, os.path.join(modulefiles, self.name()))

        if not os.path.exists(base_module_dir):
            os.makedirs(base_module_dir)

        base_content =  'local pkgNameVer  = myModuleFullName()\n'
        base_content += 'local pkgName     = myModuleName()\n'
        base_content += 'local fullVersion = myModuleVersion()\n'


        base_var = 'local base = pathJoin("%s"' % (prefix_base)
        mpath_var = 'local mpath = "%s-%s-"..fullVersion\n' % (modulefiles, self.name())
        base_content += base_var + ', fullVersion)\n'

        for (module, package, version) in deps:
            base_content += 'load("%s/%s")\n' % (package.name(), version)

        base_content += '\n'
        base_content += 'whatis("Name: "..pkgName)\n'
        base_content += 'whatis("Version: "..fullVersion)\n'
        base_content += 'whatis("Category: %s")\n' % self.get_data('category')
        base_content += 'whatis("Description: %s")\n' % self.get_data('description')
        base_content += 'whatis("URL: %s")\n' % self.get_data('url')
        base_content += 'whatis("Keywords: %s")\n' % self.get_data('keywords')
        base_content += '\n'

        base_content += 'setenv("%s_DIR", base)\n' % self.name().upper().replace('-', '_')
        base_content += 'setenv("%s_ROOT", base)\n' % self.name().upper().replace('-', '_')
        base_content += '\n'

        envs = self.module_env_vars()
        for env in envs:
            base_content += 'setenv("%s", pathJoin(base, "%s"))\n' % (env, envs[env])
        if len(envs) > 0:
            base_content += '\n'

        paths = {
            'PATH' : ['bin'],
            'LD_LIBRARY_PATH': ['lib', 'lib64'],
            'MANPATH': ['share/man'],
            'INFOPATH': ['share/info'],
            'PKG_CONFIG_PATH': ['lib/pkgconfig'],
            'PYTHONPATH': ['share/%s/python' % (self.name())]
        }
        paths.update(self.module_path_vars())
        for path in paths:
            for p in paths[path]:
                base_content += 'if (isDir(pathJoin(base, "%s"))) then\n' % (p)
                base_content += '   prepend_path("%s", pathJoin(base, "%s"))\n' %(path, p)
                base_content += 'end\n'

        base_content += '\n'
        base_content += 'if (isDir(mpath)) then\n'
        base_content += '   prepend_path("MODULEPATH", mpath)\n'
        base_content += 'end\n'
        base_content += 'local completionFile = pathJoin(base, "etc", "bash_completion.d", pkgName)\n'
        base_content += 'if (isFile(completionFile)) then\n'
        base_content += '   execute {cmd="source "..completionFile, modeA={"load"}}\n'
        base_content += 'end\n'

        f = open(base_module, 'w')
        f.write(base_content)
        f.close()

        if deps_dir == '':
            return (base_module, os.path.join(modulefiles, 'Core', self.name()))
        else:
            return (base_module, os.path.join(modulefiles, self.name()))

    def write_modulefile(self, basepath, arch, version, rext_deps, deps):
        (base_module, prefix_base) = self.base_modulefile(basepath, arch, rext_deps, deps)
        if not os.path.exists(prefix_base):
            os.makedirs(prefix_base)
        prefix_base = os.path.join(prefix_base, '%s.lua' % (version))
        if not os.path.exists(prefix_base):
            os.symlink(base_module, prefix_base)

    def is_installed(self, basepath, arch, version, deps=None):
        return os.path.exists(self.prefix(basepath, arch, version, deps))

    def install_version(self, basepath, arch, module, version, env, ext_deps = None):
        if type(version) is unicode or type(version) is str:
            for v in self.json_data['versions']:
                if v[0] == version:
                    version = v
                    break

        module_path = os.path.join(package_path, module)
        source_path = os.path.join(package_path, 'source', self.name(), version[0])
        if not os.path.exists(source_path):
            os.makedirs(source_path)

        build_env = env.copy()
        build_env['PACKAGE_VERSION'] = str(version[0])

        rext_deps = {}

        compiler = (None, ['*'])
        mpi = (None, ['*'])
        boost = (None, ['*'])
        cuda = (None, ['*'])
        python = (None, ['*'])

        for deps in self.build_deps:
            build_deps = []
            def extract_package(package, pversion):
                if pversion == '*': return (package, package.versions())
                else: return [(package, [pversion])]

            for (module, package, pversion) in deps:
                if module == 'Compiler':
                    if ext_deps and 'compiler' in ext_deps:
                        compiler = ext_deps['compiler']
                    else:
                        compiler = extract_package(package, pversion)
                    rext_deps['compiler'] = compiler
                elif module == 'MPI':
                    if ext_deps and  'mpi' in ext_deps:
                        mpi = ext_deps['mpi']
                    else:
                        mpi = extract_package(package, pversion)
                    rext_deps['mpi'] = mpi
                elif module == 'Boost':
                    if ext_deps and  'boost' in ext_deps:
                        boost = ext_deps['boost']
                    else:
                        boost = extract_package(package, pversion)
                    rext_deps['boost'] = boost
                elif module == 'CUDA':
                    if ext_deps and  'cuda' in ext_deps:
                        cuda = ext_deps['cuda']
                    else:
                        cuda = extract_package(package, pversion)
                    rext_deps['cuda'] = cuda
                elif module == 'Python':
                    if ext_deps and  'python' in ext_deps:
                        python = ext_deps['python']
                    else:
                        python = extract_package(package, pversion)
                    rext_deps['python'] = python
                else:
                    if pversion == '*':
                        raise Exception('Build script doesn\'t support wildcard versions for non Module packages')
                    build_deps.extend([(module, package, pversion)])


            print('Installing Package %s/%s.' %(
                self.name(), version[0]))

            for boost_version in boost[1]:
                if boost[0]:
                    rext_deps['boost'] = (boost[0], [boost_version])
                for cuda_version in cuda[1]:
                    if cuda[0]:
                        rext_deps['cuda'] = (cuda[0], [cuda_version])
                    for mpi_version in mpi[1]:
                        if mpi[0]:
                            rext_deps['mpi'] = (mpi[0], [mpi_version])
                        for python_version in python[1]:
                            if python[0]:
                                rext_deps['python'] = (python[0], [python_version])
                            for compiler_version in compiler[1]:
                                if compiler[0]:
                                    rext_deps['compiler'] = (compiler[0], [compiler_version])

                                if self.is_installed(basepath, arch, version[0], rext_deps):
                                    continue

                                # Recursively building dependencies and creation of module string
                                build_deps_modules = ''
                                print('Installing Dependencies for %s/%s.' %(
                                    self.name(), version[0]))

                                # Check for compiler
                                if compiler[0]:
                                    compiler[0].install_version(
                                        basepath, arch, 'Compiler', compiler_version,
                                        env, rext_deps)
                                    build_deps_modules += 'module load %s/%s\n' % (
                                        compiler[0].name(), compiler_version)

                                # Check for Python
                                if python[0]:
                                    python[0].install_version(
                                        basepath, arch, 'Python', python_version,
                                        env, rext_deps)
                                    build_deps_modules += 'module load %s/%s\n' % (
                                        python[0].name(), python_version)

                                # Check for MPI
                                if mpi[0]:
                                    mpi[0].install_version(
                                        basepath, arch, 'MPI', mpi_version,
                                        env, rext_deps)
                                    build_deps_modules += 'module load %s/%s\n' % (
                                        mpi[0].name(), mpi_version)

                                # Check for CUDA
                                if cuda[0]:
                                    cuda[0].install_version(
                                        basepath, arch, 'CUDA', cuda_version,
                                        env, rext_deps)
                                    build_deps_modules += 'module load %s/%s\n' % (
                                        cuda[0].name(), cuda_version)

                                # Check for Boost
                                if boost[0]:
                                    boost[0].install_version(
                                        basepath, arch, 'Boost', boost_version,
                                        env, rext_deps)
                                    build_deps_modules += 'module load %s/%s\n' % (
                                        cuda[0].name(), boost_version)

                                # Check for other misc deps
                                for (module, package, pversion) in build_deps:
                                    package.install_version(basepath, arch, module,
                                        pversion, env, rext_deps)
                                    build_deps_modules += 'module load %s/%s\n' % (
                                        package.name(), pversion)

                                print('Installing Dependencies for %s/%s done.' %(
                                    self.name(), version[0]))

                                src_idx = 0;
                                src_dirs={}
                                for source in version[1:]:
                                    file_name = download_source(source, source_path)
                                    src_dir = extract_source(source_path, file_name)
                                    src_dirs['SRC_DIR%s' % (src_idx)] = src_dir
                                    src_idx += 1

                                build_env.update(src_dirs)
                                build_env['BUILD_DIR'] = self.build_dir(source_path, arch,
                                    version[0], rext_deps)
                                prefix = self.prefix(basepath, arch,
                                    version[0], rext_deps)
                                build_env['PACKAGE_PREFIX'] = prefix

                                shell = subprocess.Popen(['/bin/bash', '-l'], cwd=source_path,
                                    stdin=subprocess.PIPE, env=build_env)
                                shell.stdin.write('module purge\n')
                                shell.stdin.write(build_deps_modules)
                                shell.stdin.write('module list\n')

                                for step in self.json_data['build']:
                                    shell.stdin.write('__RET=$?; if [ $__RET != 0 ]; then exit $__RET; else ' + step + '; fi\n')

                                shell.stdin.write('exit $?\n')
                                shell.stdin.flush()
                                ret = shell.wait()

                                print('')
                                if ret != 0:
                                    print('Installing Package %s/%s failed.' %(
                                        self.name(), version[0]))
                                    if os.path.exists(prefix):
                                        shutil.rmtree(prefix)
                                    exit(1)
                                self.write_modulefile(basepath, arch, version[0], rext_deps, build_deps)

        print('Installing Package %s/%s done.' %(
            self.name(), version[0]))

    def install(self, basepath, arch, module, versions = None):
        env = os.environ
        env['COLUMNS'] = '80'
        env['BUILDIT'] = 'python %s' % (os.path.realpath(__file__))
        if not versions:
            versions = self.json_data['versions']
        for version in versions:
            self.install_version(basepath, arch, module, version, env)

    def uninstall_version(self, basepath, arch, module, version, env, ext_deps = None):
        if type(version) is unicode or type(version) is str:
            for v in self.json_data['versions']:
                if v[0] == version:
                    version = v

        package = self
        compiler = None
        mpi = None
        boost = None
        cuda = None
        python = None

        for deps in package.build_deps:
            for (pmodule, ppackage, pversion) in deps:
                if pmodule == 'Compiler':
                    compiler = ppackage
                elif pmodule == 'MPI':
                    mpi = ppackage
                elif module == 'Boost':
                    boost = ppackage
                elif module == 'CUDA':
                    cuda = ppackage
                elif module == 'Python':
                    python = ppackage

        extract_versions = lambda p: p.versions() if p else ['*']

        for arch in architectures:
            deps = {}
            for boost_version in extract_versions(boost):
                if boost:
                    deps['boost'] = (boost, [boost_version])
                for cuda_version in extract_versions(cuda):
                    if cuda:
                        deps['cuda'] = (cuda, [cuda_version])
                    for mpi_version in extract_versions(mpi):
                        if mpi:
                            deps['mpi'] = (mpi, [mpi_version])
                        for python_version in extract_versions(python):
                            if python:
                                deps['python'] = (python, [python_version])
                            for compiler_version in extract_versions(compiler):
                                if compiler:
                                    deps['compiler'] = (compiler, [compiler_version])
                                if package.is_installed(basepath, arch, version[0], deps):
                                    prefix = package.prefix(basepath, arch, version[0], deps)
                                    shutil.rmtree(prefix)
                                    deps_dir = self.get_deps_path(deps)
                                    if deps_dir == '':
                                        deps_dir = 'Core'
                                    os.remove(os.path.join(basepath, arch, 'modulefiles', deps_dir, self.name(), '%s.lua' % (version[0])))
                                    print('    Removed %s: %s (%s, %s)' % (arch, version[0], deps, prefix))

    def uninstall(self, basepath, arch, module, versions = None):
        env = os.environ
        env['COLUMNS'] = '80'
        remove_base_module = False
        if not versions:
            versions = self.json_data['versions']
            remove_base_module = True
        for version in versions:
            self.uninstall_version(basepath, arch, module, version, env)

def load_packages():
    for module in os.listdir(package_path):
        module_path = os.path.join(package_path, module)

        if not module in packages:
            packages[module] = {}

        if not os.path.isdir(module_path):
            continue

        for package in os.listdir(module_path):
            package_file = os.path.join(module_path, package)
            if package_file.endswith('.json'):
                json_data = open(package_file)
                try:
                    package_data = json.load(json_data)

                    packages[module][package_data['name']] = Package(package_data)
                except:
                    print ('Could not load ' + package_file)

    for module in packages:
        for name in packages[module]:
            packages[module][name].resolve_dependencies()

def list_installed(basepath):
    print ('Listing installed packages in ' + basepath)
    for module in packages:
        for name in packages[module]:
            package = packages[module][name]
            print('%s/%s:' % (module, name))
            compiler = None
            mpi = None
            boost = None
            cuda = None
            python = None

            for deps in package.build_deps:
                for (pmodule, ppackage, pversion) in deps:
                    if pmodule == 'Compiler':
                        compiler = ppackage
                    elif pmodule == 'MPI':
                        mpi = ppackage
                    elif module == 'Boost':
                        boost = ppackage
                    elif module == 'CUDA':
                        cuda = ppackage
                    elif module == 'Python':
                        python = ppackage

            extract_versions = lambda p: p.versions() if p else ['*']

            for arch in architectures:
                for version in package.versions():
                    deps = {}
                    for boost_version in extract_versions(boost):
                        if boost:
                            deps['boost'] = (boost, [boost_version])
                        for cuda_version in extract_versions(cuda):
                            if cuda:
                                deps['cuda'] = (cuda, [cuda_version])
                            for mpi_version in extract_versions(mpi):
                                if mpi:
                                    deps['mpi'] = (mpi, [mpi_version])
                                for python_version in extract_versions(python):
                                    if python:
                                        deps['python'] = (python, [python_version])
                                    for compiler_version in extract_versions(compiler):
                                        if compiler:
                                            deps['compiler'] = (compiler, [compiler_version])
                                        if package.is_installed(basepath, arch, version, deps):
                                            print('    %s: %s (%s)' % (arch, version, deps))

def list_available():
    for module in packages:
        for package in packages[module]:
            print (packages[module][package])

def install(basepath, targets, arch):
    print ('Installing \'%s\' to %s' % (targets, basepath))

    archs = architectures
    if arch != 'all':
        if not arch in architectures:
            error = '%s not found in list of architectures %s' % (arch, architectures)
            raise Exception(error)
        archs = [arch]

    versions = None
    if targets == 'all':
        install_packages = packages
    else:
        (module, package, version) = find_package(targets)
        if not package:
            print('Could not find package %s' % (targets))
            exit(1)
        if version != '*':
            versions = [version]
        install_packages = {module: {package.name(): package}}

    for arch in archs:
        for module in install_packages:
            for name in install_packages[module]:
                package = install_packages[module][name]
                package.install(basepath, arch, module, versions)

def uninstall(basepath, targets, arch):
    print ('Uninstalling \'%s\' to %s' % (targets, basepath))

    archs = architectures
    if arch != 'all':
        if not arch in architectures:
            error = '%s not found in list of architectures %s' % (arch, architectures)
            raise Exception(error)
        archs = [arch]

    versions = None
    if targets == 'all':
        uninstall_packages = packages
    else:
        (module, package, version) = find_package(targets)
        if not package:
            print('Could not find package %s' % (targets))
            exit(1)
        if version != '*':
            versions = [version]
        uninstall_packages = {module: {package.name(): package}}

    for arch in archs:
        for module in uninstall_packages:
            for name in uninstall_packages[module]:
                package = uninstall_packages[module][name]
                package.uninstall(basepath, arch, module, versions)

def main():
    parser = argparse.ArgumentParser(description='build.py')
    parser.add_argument('--basepath', default='/opt/apps',
        help='Base path for the packages to be installed')
    parser.add_argument('--arch', default='all',
        help='The architectures to build for')
    parser.add_argument('--list', action='store_const', const=True, default=False,
        help='Lists all the installed packages')
    parser.add_argument('--available', action='store_const', const=True, default=False,
        help='Lists all the available packages')
    parser.add_argument('--uninstall', action='store_const', const=True, default=False,
        help='Uninstalls the software package (default=all)')
    parser.add_argument('--install', action='store_const', const=True, default=False,
        help='Installs the software package')
    parser.add_argument('--targets', default='all',
        help='The targets for (un)installation (default=all)')

    args = parser.parse_args()

    command_list = [args.list, args.available, args.install, args.uninstall]

    # Check if we have any of the commands
    if (reduce(lambda opt1, opt2: opt1 or opt2, command_list, False)):
        # Check if we don't have two commands at the same time:
        if (reduce(lambda opt1, opt2: not opt2 if (opt1) else opt2, command_list, True)):
            print ('Please provide only of --list, --available, --uninstall, --install')
            exit(1)
    else:
        print ('Please provide one of --list, --available, --uninstall, --install')
        exit(1)

    packages = load_packages()

    basepath = args.basepath

    if not os.path.exists(basepath):
        os.makedirs(basepath)

    if (args.list):
        list_installed(basepath)
        return

    if (args.available):
        list_available()
        return

    if (args.install):
        install(basepath, args.targets, args.arch)
        return

    if (args.uninstall):
        uninstall(basepath, args.targets, args.arch)
        return

if __name__ == '__main__':
    main()
