"""Typed SerpApi adapter. No product code should construct raw SerpApi URLs."""

from app.services.serpapi.client import SerpApiClient, SerpApiError, SerpApiResult

__all__ = ["SerpApiClient", "SerpApiError", "SerpApiResult"]
