#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vim: set ts=4 et

import os, sys
import subprocess
import tempfile
import ConfigParser

# HACK for best compression
import zipfile, zlib
zlib.Z_DEFAULT_COMPRESSION = zlib.Z_BEST_COMPRESSION
class ZipFile(zipfile.ZipFile):
    def __init__(self, file, mode="r"):
        zipfile.ZipFile.__init__(self, file, mode, zipfile.ZIP_DEFLATED)


rootdir = os.path.dirname(os.path.realpath(__file__))
debug = os.path.basename(os.environ['B']).startswith('debug-')


def pk3_version(file):
    if os.path.exists(file):
        with ZipFile(file, 'r') as zf:
            try:
                return zf.read('pk3_version')
            except:
                pass


def src_version(src):
    old_cwd = os.getcwd()
    os.chdir(rootdir)

    args = ['git', 'status', '--porcelain']
    for fn in src:
        status = subprocess.check_output(args + [fn]) or None
        if status:
            return None

    args = ['git', 'log', '-1', '--format=format:%H', '--']
    version = subprocess.check_output(args + src) or None

    os.chdir(old_cwd)
    return version


def generate_pk3_info(config, filename):
    pk3_info = {}
    pk3_info['filename'] = filename
    pk3_info['file'] = os.path.join(os.environ['B'], 'base', filename)

    files = config.get(filename, 'files')
    pk3_info['files'] = [fn.strip() for fn in files.split('\n') if fn]

    try:
        pk3_info['root'] = config.get(filename, 'root')
    except ConfigParser.NoOptionError:
        pk3_info['root'] = ''

    try:
        src = config.get(filename, 'src')
        pk3_info['src'] = [fn.strip() for fn in src.split('\n') if fn]
    except ConfigParser.NoOptionError:
        pk3_info['src'] = [os.path.join(pk3_info['root'], fn) for fn in pk3_info['files']]

    pk3_info['version'] = src_version(pk3_info['src'])
    return pk3_info


def build_pk3(pk3_info):
    os.chdir(rootdir)

    os.chdir(os.path.join(rootdir, pk3_info['root']))
    with ZipFile(os.path.join(rootdir, pk3_info['file']), 'w') as zf:
        for fn in pk3_info['files']:
            if not os.path.exists(fn):
                sys.exit('%s: file not found: %s' % (pk3_info['filename'], os.path.join(pk3_info['root'], fn)))
            zf.write(fn)

        if not debug and pk3_info['version']:
            zf.writestr('pk3_version', pk3_info['version'])


def profile_get(config, option):
    section = 'debug-profile' if debug else 'release-profile'
    try:
        return config.get('any-profile', option)
    except ConfigParser.NoOptionError:
        return config.get(section, option)


def tremded_command(config, command):
    host = profile_get(config, 'host')
    game = profile_get(config, 'game')

    args = ['ssh', host, 'bin/tremded', game, command]
    if command == 'running':
        return subprocess.call(args) == 0
    else:
        subprocess.check_call(args)


def remote_version(config, pk3_file):
    host = profile_get(config, 'host')
    game = profile_get(config, 'game')

    f = tempfile.NamedTemporaryFile(delete=False)
    temp = f.name
    f.close()
    args = ['scp', '-q', '%s:tremded/pk3/%s/%s' % (host, game, pk3_file), temp]
    if subprocess.call(args) != 0:
        return None

    version = pk3_version(temp)
    os.unlink(temp)
    return version


def deploy_pk3(config, pk3_info):
    host = profile_get(config, 'host')
    game = profile_get(config, 'game')
    fs_game = profile_get(config, 'fs_game')

    args = ['scp', '-q', pk3_info['file'], '%s:tremded/pk3/%s' % (host, game)]
    subprocess.check_call(args)

    commands = """\
      chmod 660 tremded/pk3/%(game)s/%(filename)s;
      ln -fs ../../pk3/%(game)s/%(filename)s tremded/%(game)s/%(fs_game)s/%(filename)s
    """ % {'game': game, 'fs_game': fs_game, 'filename': pk3_info['filename']}

    args = ['ssh', host, commands]
    subprocess.check_call(args)


def deploy_extra(config, filename):
    host = profile_get(config, 'host')
    game = profile_get(config, 'game')
    fs_game = profile_get(config, 'fs_game')
 
    f = tempfile.NamedTemporaryFile(delete=False)
    temp = f.name
    f.close()
    args = ['scp', '-q', '%s:tremded/%s/%s/%s' % (host, game, fs_game, os.path.basename(filename)), temp]
    if subprocess.call(args) == 0:
        args = ['diff', filename, temp]
        if subprocess.call(args) == 0:
            return False
    os.unlink(temp)
   
    args = ['scp', '-q', filename, '%s:tremded/%s/%s' % (host, game, fs_game)]
    subprocess.check_call(args)

    commands = """\
      chmod 660 tremded/%(game)s/%(fs_game)s/%(filename)s;
    """ % {'game': game, 'fs_game': fs_game, 'filename': os.path.basename(filename)}

    args = ['ssh', host, commands]
    subprocess.check_call(args)
    return True


if __name__ == '__main__':
    defaults = {
        'ARCH':     os.environ['ARCH'],
        'B':        os.environ['B'],
        'SHLIBEXT': os.environ['SHLIBEXT'],
    }
    config = ConfigParser.SafeConfigParser(defaults)
    config.read(os.path.join(rootdir, 'deploy.cfg'))

    ### BUILD ###
    pk3_files = []

    sys.stderr.write('Building pk3 files...\n')
    for section in config.sections():
        if not section.endswith('.pk3'):
            continue

        pk3_info = generate_pk3_info(config, section)
        pk3_files.append(pk3_info)

        if not debug:
            if not pk3_info['version']:
                sys.stderr.write('    skipped   %s (uncomitted changes)\n' % pk3_info['filename'])
                continue

            if pk3_info['version'] == pk3_version(pk3_info['file']):
                sys.stderr.write('    skipped   %s (already up to date)\n' % pk3_info['filename'])
                continue

        sys.stderr.write('    building  %s\n' % pk3_info['filename'])
        build_pk3(pk3_info)

    ### DEPLOY ###
    deployed = False
    os.chdir(rootdir)

    sys.stderr.write('Deploying...\n')
    for pk3_info in pk3_files:
        if not pk3_info['version']:
            sys.stderr.write('    skipped   %s (not built)\n' % pk3_info['filename'])
            continue

        version = remote_version(config, pk3_info['filename'])
        if not debug and version == pk3_info['version']:
            sys.stderr.write('    skipped   %s (already up to date)\n' % pk3_info['filename'])
            continue

        sys.stderr.write('    deploying %s\n' % pk3_info['filename'])
        deploy_pk3(config, pk3_info)
        deployed = True

    try:
        extra_files = profile_get(config, 'extra_files')
        extra_files = [fn.strip() for fn in extra_files.split('\n') if fn]
    except ConfigParser.NoOptionError:
        extra_files = []
    
    for fn in extra_files:
        filename = os.path.basename(fn)
        rv = deploy_extra(config, fn)
        if rv:
            sys.stderr.write('    deployed  %s\n' % filename)
            deployed = True
        else:
            sys.stderr.write('    skipped   %s (already up to date)\n' % filename)

    ### START/RESTART ###
    if deployed:
        if tremded_command(config, 'running'):
            tremded_command(config, 'detach')
            tremded_command(config, 'stop')
        tremded_command(config, 'start')

