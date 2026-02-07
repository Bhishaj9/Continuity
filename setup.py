from setuptools import setup, find_packages

setup(
    name='videostitcher',
    version='0.1.0',
    packages=find_packages(),
    install_requires=[
        # Assume ffmpeg is installed at system level, no python bindings needed for this subprocess implementation
    ],
)
