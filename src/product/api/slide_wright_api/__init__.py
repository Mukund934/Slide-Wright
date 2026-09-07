"""The local product API.

ADR-0010: one Python process serves the product. This package imports the
engine directly rather than shelling out to it, and binds to loopback only.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
