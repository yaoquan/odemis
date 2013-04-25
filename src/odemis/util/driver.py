# -*- coding: utf-8 -*-
'''
Created on 5 Mar 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS F

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import collections
import logging
import os
import sys

def getSerialDriver(name):
    """
    return (string): the name of the serial driver used for the given port
    """
    # In linux, can be found as link of /sys/class/tty/tty*/device/driver
    if sys.platform.startswith('linux'):
        path = "/sys/class/tty/" + os.path.basename(name) + "/device/driver"
        try:
            return os.path.basename(os.readlink(path))
        except OSError:
            return "Unknown"
    else:
        # TODO: Windows version
        return "Unknown"

# String -> VA conversion helper
def boolify(s):
    if s == 'True' or s == 'true':
        return True
    if s == 'False' or s == 'false':
        return False
    raise ValueError('Not a boolean value: %s' % s)

def reproduceTypedValue(real_val, str_val):
    """
    Tries to convert a string to the type of the given value
    real_val (object): value with the type that must be converted to
    str_val (string): string that will be converted
    return the value contained in the string with the type of the real value
    raises
      ValueError() if not possible to convert
      TypeError() if type of real value is not supported
    """
    if isinstance(real_val, bool):
        return boolify(str_val)
    elif isinstance(real_val, int):
        return int(str_val)
    elif isinstance(real_val, float):
        return float(str_val)
    elif isinstance(real_val, basestring):
        return str_val
    elif isinstance(real_val, dict): # must be before iterable
        if len(real_val) > 0:
            key_real_val = real_val.keys()[0]
            value_real_val = real_val[key_real_val]
        else:
            logging.warning("Type of attribute is unknown, using string")
            sub_real_val = ""
            value_real_val = ""

        dict_val = {}
        for sub_str in str_val.split(','):
            item = sub_str.split(':')
            assert(len(item) == 2)
            key = reproduceTypedValue(key_real_val, item[0]) # TODO Should warn if len(item) != 2
            value = reproduceTypedValue(value_real_val, item[1])
            dict_val[key] = value
        return dict_val
    elif isinstance(real_val, collections.Iterable):
        if len(real_val) > 0:
            sub_real_val = real_val[0]
        else:
            logging.warning("Type of attribute is unknown, using string")
            sub_real_val = ""

        iter_val = [] # the most preserving iterable
        for sub_str in str_val.split(','): # TODO accept at least "x", or even any value which is not a number (if we know inside is a number)
            iter_val.append(reproduceTypedValue(sub_real_val, sub_str))
        final_val = type(real_val)(iter_val) # cast to real type
        return final_val

    raise TypeError("Type %r is not supported to convert %s" % (type(real_val), str_val))
