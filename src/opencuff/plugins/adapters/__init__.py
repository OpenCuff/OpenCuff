"""Plugin adapters for different plugin types.

Adapters handle the communication protocol for each plugin type:
    - InSourceAdapter: Loads Python modules via importlib
    - ProcessAdapter: Communicates via JSON over stdin/stdout (future)
    - HTTPAdapter: Communicates via HTTP REST API (future)
"""

from opencuff.plugins.adapters.in_source import InSourceAdapter

__all__ = ["InSourceAdapter"]
