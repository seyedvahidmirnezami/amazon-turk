from setuptools import setup

setup(
    name='qualtrics_tagger',
    version='1.0.0',
    packages=['qualtrics_tagger'],
    url='https://bitbucket.org/baskargroup/qualtrics_tagger/',
    license='',
    author='Alec Lofquist',
    author_email='alces14@gmail.com',
    description='Module for creating and managing image annotation surveys using the Qualtrics platform.',
    extras_require={
        'automatic copy-to-clipboard': ['pyperclip'],
        'image downscaling': ['scipy', 'Pillow'],
    },
    python_requires='>=3.5'
)
