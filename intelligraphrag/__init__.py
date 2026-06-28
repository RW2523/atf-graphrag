"""ATF Configurable GraphRAG Platform.

A configurable, multi-environment GraphRAG system for ATF-related data.
All model requests (LLM, embeddings, vision, rerank) route through providers
that are selected by configuration. Default local profile uses OpenRouter.ai
for LLM/vision and a dependency-free local embedder, so the app runs anywhere.
"""

__version__ = "1.0.0"
