#
# Copyright (c) 2010-2013 Liraz Siri <liraz@turnkeylinux.org>
#
# This file is part of TKLBAM (TurnKey Linux BAckup and Migration).
#
# TKLBAM is open source software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of
# the License, or (at your option) any later version.
#
import sys

import os
from os.path import *

import resource

import shutil
import commands

import userdb
from paths import Paths
from changes import Changes
from pathmap import PathMap
from dirindex import DirIndex
from pkgman import Installer
from rollback import Rollback
from temp import TempDir

import utils

import backup
import conf
import mysql
import duplicity

from squid import Squid

class Error(Exception):
    pass

RLIMIT_NOFILE_MAX = 8192

def raise_rlimit(type, newlimit):
    soft, hard = resource.getrlimit(type)
    if soft > newlimit:
        return

    if hard > newlimit:
        return resource.setrlimit(type, (newlimit, hard))

    try:
        resource.setrlimit(type, (newlimit, newlimit))
    except ValueError:
        return

def system(command):
    sys.stdout.flush()
    sys.stderr.flush()
    return os.system(command)

class Restore:
    Error = Error

    PACKAGES_BLACKLIST = ['linux-*', 'vmware-tools*']

    @staticmethod
    def _title(title, c='='):
        return title + "\n" + c * len(title) + "\n"

    @staticmethod
    def _duplicity_restore(address, cache_size, cache_dir, credentials, secret, time=None, download_path=None):
        if not download_path:
            download_path = TempDir(prefix="tklbam-")
            os.chmod(download_path, 0700)

        if time:
            opts = [("restore-time", time)]
        else:
            opts = []

        squid = Squid(cache_size, cache_dir)
        squid.start()

        orig_env = os.environ.get('http_proxy')
        os.environ['http_proxy'] = squid.address

        raise_rlimit(resource.RLIMIT_NOFILE, RLIMIT_NOFILE_MAX)
        duplicity.Command(opts, '--s3-unencrypted-connection', address, download_path).run(secret, credentials)

        if orig_env:
            os.environ['http_proxy'] = orig_env
        else:
            del os.environ['http_proxy']

        sys.stdout.flush()

        squid.stop()

        return download_path

    def __init__(self, address, secret, cache_size, cache_dir,
                 limits=[], time=None, credentials=None, rollback=True, download_path=None):
        print "Restoring duplicity archive from " + address
        backup_archive = self._duplicity_restore(address, cache_size, cache_dir, credentials, secret, time, download_path)

        extras_path = backup_archive + backup.ExtrasPaths.PATH
        self.extras = backup.ExtrasPaths(extras_path)
        self.rollback = Rollback.create() if rollback else None
        self.limits = conf.Limits(limits)
        self.credentials = credentials
        self.backup_archive = backup_archive

    def database(self):
        if not exists(self.extras.myfs):
            return

        if self.rollback:
            self.rollback.save_database()

        print "\n" + self._title("Restoring databases")

        try:
            mysql.restore(self.extras.myfs, self.extras.etc.mysql,
                          limits=self.limits.db, callback=mysql.cb_print())

        except mysql.Error, e:
            print "SKIPPING MYSQL DATABASE RESTORE: " + str(e)

    def packages(self):
        newpkgs_file = self.extras.newpkgs
        if not exists(newpkgs_file):
            return

        print "\n" + self._title("Restoring new packages")

        # apt-get update, otherwise installer may skip everything
        print self._title("apt-get update", '-')
        system("apt-get update")

        packages = file(newpkgs_file).read().strip().split('\n')
        installer = Installer(packages, self.PACKAGES_BLACKLIST)

        print "\n" + self._title("apt-get install", '-')
        if installer.skipping:
            print "SKIPPING: " + " ".join(installer.skipping) + "\n"

        if not installer.command:
            print "NO NEW INSTALLABLE PACKAGES"
            return

        print installer.command
        exitcode = installer()
        if exitcode != 0:
            print "# WARNING: non-zero exitcode (%d)" % exitcode

        if self.rollback:
            self.rollback.save_new_packages(installer.installed)

    @staticmethod
    def _userdb_merge(old_etc, new_etc):
        old_passwd = join(old_etc, "passwd")
        new_passwd = join(new_etc, "passwd")

        old_group = join(old_etc, "group")
        new_group = join(new_etc, "group")

        def r(path):
            return file(path).read()

        return userdb.merge(r(old_passwd), r(old_group),
                            r(new_passwd), r(new_group))

    @staticmethod
    def _iter_apply_overlay(overlay, root, limits=[]):
        def walk(dir):
            fnames = []
            subdirs = []

            for dentry in os.listdir(dir):
                path = join(dir, dentry)

                if not islink(path) and isdir(path):
                    subdirs.append(path)
                else:
                    fnames.append(dentry)

            yield dir, fnames

            for subdir in subdirs:
                for val in walk(subdir):
                    yield val

        class OverlayError:
            def __init__(self, path, exc):
                self.path = path
                self.exc = exc

            def __str__(self):
                return "OVERLAY ERROR @ %s: %s" % (self.path, self.exc)

        pathmap = PathMap(limits)
        overlay = overlay.rstrip('/')
        for overlay_dpath, fnames in walk(overlay):
            root_dpath = root + overlay_dpath[len(overlay) + 1:]

            for fname in fnames:
                overlay_fpath = join(overlay_dpath, fname)
                root_fpath = join(root_dpath, fname)

                if root_fpath not in pathmap:
                    continue

                try:
                    if not isdir(root_dpath):
                        if exists(root_dpath):
                            os.remove(root_dpath)
                        os.makedirs(root_dpath)

                    if lexists(root_fpath):
                        utils.remove_any(root_fpath)

                    utils.move(overlay_fpath, root_fpath)
                    yield root_fpath
                except Exception, e:
                    yield OverlayError(root_fpath, e)

    def files(self):
        extras = self.extras
        if not exists(extras.fsdelta):
            return

        overlay = self.backup_archive
        rollback = self.rollback
        limits = self.limits.fs

        print "\n" + self._title("Restoring filesystem")

        print "MERGING USERS AND GROUPS\n"
        passwd, group, uidmap, gidmap = self._userdb_merge(extras.etc, "/etc")

        for olduid in uidmap:
            print "UID %d => %d" % (olduid, uidmap[olduid])
        for oldgid in gidmap:
            print "GID %d => %d" % (oldgid, gidmap[oldgid])

        changes = Changes.fromfile(extras.fsdelta, limits)
        deleted = list(changes.deleted())

        if rollback:
            rollback.save_files(changes, overlay)

        print "\nOVERLAY:\n"
        for val in self._iter_apply_overlay(overlay, "/", [ "-" + backup.ExtrasPaths.PATH ] + limits):
            print val

        emptydirs = list(changes.emptydirs())
        statfixes = list(changes.statfixes(uidmap, gidmap))

        if emptydirs or statfixes or deleted:
            print "\nPOST-OVERLAY FIXES:\n"

        for action in emptydirs:
            print action
            action()

        for action in statfixes:
            print action
            action()

        for action in deleted:
            print action

            # rollback moves deleted to 'originals'
            if not rollback:
                action()

        def w(path, s):
            file(path, "w").write(str(s))

        w("/etc/passwd", passwd)
        w("/etc/group", group)

