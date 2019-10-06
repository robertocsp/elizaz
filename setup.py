#!/usr/bin/env python
import os
from distutils.core import setup


# with open(os.path.join(os.path.dirname(__file__), 'README.rst')) as readme:
#     README = readme.read()

# allow setup.py to be run from any path
from setuptools import find_packages

os.chdir(os.path.normpath(os.path.join(os.path.abspath(__file__), os.pardir)))

setup(
    name='amazonseller',
    version='0.1',
    packages=find_packages(),
    include_package_data=True,
    # license='BSD License',
    description='A simple Django app to manage Amazon Sellers Accounts using Amazon MWS.',
    # long_description=README,
    # url='https://www.example.com/',
    author='Gustavo',
    author_email='gustavo.guly@gmail.com',
    classifiers=[
        'Environment :: Web Environment',
        'Framework :: Django',
        'Framework :: Django :: 2.2.5',
        'Intended Audience :: Developers',
        # 'License :: OSI Approved :: BSD License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Topic :: Internet :: WWW/HTTP',
        'Topic :: Internet :: WWW/HTTP :: Dynamic Content',
    ],
)
