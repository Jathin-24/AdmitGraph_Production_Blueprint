"""Profile router data access.

Row handling for ``/me/profile`` lives in ``app.services.profile``; the
router's only session operation is the transaction commit that persists the
service's writes, re-exported here so every ``session`` call stays in the
repository layer.
"""

from app.db.repositories.base import commit as commit
