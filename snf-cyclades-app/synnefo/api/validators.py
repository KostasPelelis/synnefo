# Copyright (C) 2010-2014 GRNET S.A.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import unicodedata
import six
import re


def is_printable(c):
    try:
        c.encode('utf-8')
        return True
    except UnicodeEncodeError:
        return False


def build_regex_range(ws=True, exclude=None):

    if exclude is None:
        exclude = {}
    else:
        exclude = {k: None for k in exclude}

    regex = ""
    last = None
    last_added = None
    in_range = False

    def is_valid(c):
        if c in exclude:
            return False
        elif ws:
            return is_printable(c)

        return (is_printable(c) and unicodedata.category(c) != "Zs")

    for c in range(0xFFFF):
        c = six.unichr(c)
        if is_valid(c):
            if not in_range:
                regex += re.escape(c)
                last_added = c
            in_range = True
        else:
            if in_range and last != last_added:
                regex += "-" + re.escape(last)
            in_range = False
        last = c
    else:
        if in_range:
            regex += "-" + re.escape(c)
    return regex

name_leading_trailing_spaces = (
    "[%(ws)s]*[%(no_ws)s]+[%(ws)s]*|"
    "[%(ws)s]*[%(no_ws)s][%(no_ws)s%(ws)s]+[%(no_ws)s][%(ws)s]*") % {
        'ws': build_regex_range(),
        'no_ws': build_regex_range(ws=False)
    }
