"""InBody MCP server using the mobile REST API."""

from .client import InBodyClient, InBodyError

__all__ = ["InBodyClient", "InBodyError"]
