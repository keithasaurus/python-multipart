#!/usr/bin/env python
from setuptools import setup

import os
import re

version_file = os.path.join('multipart', '_version.py')
with open(version_file, 'rb') as f:
    version_data = f.read().strip().decode('ascii')


version_re = re.compile(r'((?:\d+)\.(?:\d+)\.(?:\d+))')
version = version_re.search(version_data).group(0)

setup(name='python-multipart',
      version=version,
      description='A streaming multipart parser for Python',
      author='Andrew Dunham',
      url='http://github.com/andrew-d/python-multipart',
      license='Apache',
      platforms='any',
      zip_safe=False,
      packages=[
          'multipart',
      ],
      classifiers=[
        'Development Status :: 3 - Alpha',
        'Environment :: Web Environment',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: Apache Software License',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Topic :: Software Development :: Libraries :: Python Modules'
      ],
      test_suite='multipart.tests.suite',)
