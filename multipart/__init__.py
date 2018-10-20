from __future__ import absolute_import

# This is the canonical package information.
__author__ = 'Andrew Dunham'
__license__ = 'Apache'
__copyright__ = "Copyright (c) 2012-2013, Andrew Dunham"

# We get the version from a sub-file that can be automatically generated.
from ._version import __version__  # noqa: F401

from .multipart import (  # noqa: F401
    FormParser,
    MultipartParser,
    QuerystringParser,
    OctetStreamParser,
    create_form_parser,
    parse_form,
)
