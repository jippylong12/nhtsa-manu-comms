"""Read API over the canonical Postgres corpus (Phase A).

Additive to the Mongo-backed communications feature: these endpoints serve the
processed corpus (LLM summaries, tags, full-text search) while the existing
Mongo endpoints keep working until the frontend has fully switched.
"""
