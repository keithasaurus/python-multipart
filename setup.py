#!/usr/bin/env python
import os
import re
from setuptools import setup

version_file = os.path.join('multipart', '_version.py')
with open(version_file, 'rb') as f:
    version_data = f.read().strip().decode('ascii')


version_re = re.compile(r'((?:\d+)\.(?:\d+)\.(?:\d+))')
version = version_re.search(version_data).group(0)

tests_require = [
    'pytest',
    'pytest-cov',
    'PyYAML'
]

setup(name='python-multipart',
      version=version,
      description='A streaming multipart parser for Python',
      author='Andrew Dunham',
      url='http://github.com/andrew-d/python-multipart',
      license='Apache',
      platforms='any',
      zip_safe=False,
      install_requires=[
          'six>=1.4.0',
      ],
      tests_require=tests_require,
      packages=[
          'multipart',
      ],
      classifiers=[
        'Development Status :: 3 - Alpha',
        'Environment :: Web Environment',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: Apache Software License',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.3',
        'Topic :: Software Development :: Libraries :: Python Modules'
      ],
      test_suite='multipart.tests.suite',)

