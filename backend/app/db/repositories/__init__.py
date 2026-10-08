"""Repository layer: every SQL statement the API needs lives in this package.

Routers hand the request-scoped ``AsyncSession`` to these functions and shape
what comes back into HTTP responses (BACKEND_SPEC "Layering":
router -> service -> repository -> database). One module per router domain.
"""
