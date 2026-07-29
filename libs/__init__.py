# -*- coding: utf-8 -*-
"""Package bootstrap.

`resources/lib` holds the vendored python-jose (and the ecdsa it pulls in),
which Kodi does not put on the path by itself -- that only happens for
`xbmc.python.module` addons. Doing it here means every entry point gets it:
importing anything from `libs` runs this first.
"""
import os
import sys

_LIB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'resources', 'lib')

if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
