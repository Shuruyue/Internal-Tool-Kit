"""Independent, tested modules for small internal tools.

Each module stands alone and can be used without the others; none of them draws a user interface.
See README.md for the list of modules and how they fit together.
"""

from internal_tool_kit.i18n import KitError, Message, set_default_language

__all__ = ["KitError", "Message", "set_default_language"]
__version__ = "0.1.0"
