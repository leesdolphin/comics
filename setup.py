#!/usr/bin/env python3

from setuptools import setup


with open('README.rst') as readme_file:
    readme = readme_file.read()

with open('HISTORY.rst') as history_file:
    history = history_file.read()

requirements = [
    'aiohttp',
    'beautifulsoup4',
    'PyYAML',
    'jinja2'
]

test_requirements = [
    'flake8',
    'flake8-import-order',
    'pep8-naming'
]

setup(
    name='comics',
    version='0.0.1',
    description="Comics get downloaded. It's also async!!",
    long_description=readme + '\n\n' + history,
    author="Lee Symes",
    author_email='leesdolphin@gmail.com',
    url='https://github.com/leesdolphin/comics',
    packages=[
        'comic',
    ],
    package_dir={'comic': 'comic'},
    entry_points={
        'console_scripts': [
            'dl_comic = comic.core:main',
        ],
    },
    include_package_data=True,
    install_requires=requirements,
    license="GPL3",
    zip_safe=False,
    keywords='',
    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'Environment :: Console',
        'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
        'Natural Language :: English',
        'Programming Language :: Python :: 3.5',
    ],
    test_suite='tests',
    tests_require=test_requirements
)
