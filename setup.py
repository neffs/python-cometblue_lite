# -*- coding: utf-8 -*-
from setuptools import setup

setup(
    name='cometblue_lite',
    version='0.3-rc3',
    packages=['cometblue_lite'],
    python_requires='>=3.4',
    install_requires=['bluepy>=1.3'],
    description='Module for Eurotronic Comet Blue thermostats',
    author='David Kreitschmann',
    maintainer='David Kreitschmann',
    url='https://github.com/neffs/python-cometblue-lite',
    license="MIT",
    classifiers=[
            "Programming Language :: Python :: 3",
            "License :: OSI Approved :: MIT License",
            "Operating System :: POSIX :: Linux",
            "Development Status :: 3 - Alpha",
        ],
)
