"""Automatic memory system for Muse Code CLI.

Three scopes (global, project-personal, project-shared), hook-driven capture,
BM25-hybrid RAG recall, and automated dedupe/merge/expire/trim maintenance.
Standard library only.
"""

from . import config, consolidate, extractor, lifecycle, listing, paths, rag, store

__version__ = "1.0.0"
__all__ = ["config", "consolidate", "extractor", "lifecycle", "listing",
           "paths", "rag", "store"]
