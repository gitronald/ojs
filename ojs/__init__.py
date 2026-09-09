"""Tools for working with the Open Journal Systems (OJS) API."""

import logging

__version__ = "0.9.1a0"

# Progress and warnings go to the `ojs` logger, never to print(): a library must
# not write to a caller's console uninvited. The NullHandler keeps the package
# silent by default; `ojs.cli` attaches a stdout handler so the commands print
# exactly what they always have, and an embedding caller opts in the same way.
logging.getLogger(__name__).addHandler(logging.NullHandler())
