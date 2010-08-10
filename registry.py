# the registry is implemented as a virtual object with properties that are
# mapped directly to files.
# 
# Copyright (c) 2010 Liraz Siri <liraz@turnkeylinux.org>
# 
# This file is part of TKLBAM (TurnKey Linux BAckup and Migration).
# 
# TKLBAM is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of
# the License, or (at your option) any later version.
# 

import sys
import os
from os.path import *
from paths import Paths

import shutil
from utils import AttrDict
from hub import Credentials

class UNDEFINED:
    pass

class _Registry(object):
    class Paths(Paths):
        files = ['sub_apikey', 'secret', 'key', 'credentials', 'hbr', 'profile', 'profile/stamp']

    def __init__(self, path=None):
        if path is None:
            path = os.environ.get('TKLBAM_REGISTRY', '/var/lib/tklbam')

        if not exists(path):
            os.makedirs(path)
            os.chmod(path, 0700)

        self.path = self.Paths(path)

    @staticmethod
    def _file_str(path, s=UNDEFINED):
        if s is UNDEFINED:
            if not exists(path):
                return None

            return file(path).read().rstrip()

        else:
            if s is None:
                if exists(path):
                    os.remove(path)
            else:
                fh = file(path, "w")
                os.chmod(path, 0600)
                print >> fh, s
                fh.close()

    @classmethod
    def _file_tuple(cls, path, t=UNDEFINED):
        if t and t is not UNDEFINED:
            t = "\n".join([ str(v) for v in t ])

        retval = cls._file_str(path, t)
        if retval:
            return tuple(retval.split('\n'))

    @classmethod
    def _file_dict(cls, path, d=UNDEFINED):
        if d and d is not UNDEFINED:
            d = "\n".join([ "%s=%s" % (k, v) for k, v in d.items() ])

        retval = cls._file_str(path, d)
        if retval:
            return AttrDict([ v.split("=", 1) for v in retval.split("\n") ])

    def sub_apikey(self, val=UNDEFINED):
        return self._file_str(self.path.sub_apikey, val)
    sub_apikey = property(sub_apikey, sub_apikey)

    def secret(self, val=UNDEFINED):
        return self._file_str(self.path.secret, val)
    secret = property(secret, secret)

    def key(self, val=UNDEFINED):
        return self._file_str(self.path.key, val)
    key = property(key, key)

    def credentials(self, val=UNDEFINED):
        if val and val is not UNDEFINED:
            val = AttrDict({'accesskey': val.accesskey,
                            'secretkey': val.secretkey,
                            'usertoken': val.usertoken,
                            'producttoken': val.producttoken})

        retval = self._file_dict(self.path.credentials, val)
        if retval:
            return Credentials(retval)

    credentials = property(credentials, credentials)
    
    def hbr(self, val=UNDEFINED):
        if val and val is not UNDEFINED:
            val = AttrDict({'address': val.address,
                            'backup_id': val.backup_id})
        return self._file_dict(self.path.hbr, val)
    hbr = property(hbr, hbr)

    def profile(self, val=UNDEFINED):
        if val is None:
            return shutil.rmtree(self.path.profile, ignore_errors=True)

        if val is UNDEFINED:
            if not exists(self.path.profile.stamp):
                return None

            timestamp = os.stat(self.path.profile.stamp).st_mtime
            return Profile(self.path.profile, timestamp)
        else:
            profile_archive = val

            if not exists(self.path.profile):
                os.makedirs(self.path.profile)

            profile_archive.extract(self.path.profile)
            file(self.path.profile.stamp, "w").close()
            os.utime(self.path.profile.stamp, (0, profile_archive.timestamp))
    profile = property(profile, profile)

class Profile(str):
    def __new__(cls, path, timestamp):
        return str.__new__(cls, path)

    def __init__(self, path, timestamp):
        self.timestamp = timestamp

registry = _Registry()
