"""Extraction utilities (entity resolution / cross-document linking)."""
from .entity_resolution import (normalise, EntityResolver,
                                remap_relationships)

__all__ = ["normalise", "EntityResolver", "remap_relationships"]
