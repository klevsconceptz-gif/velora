"""Velora — a creator membership platform.

Architecture
------------
* ``server/``  Python 3.11 standard-library API (WSGI) with a SQLite store.
* ``web/``     Dependency-free vanilla JavaScript single-page frontend.

Design rules encoded here
-------------------------
* Production-sensitive settings are absent by default. With no BTCPay
  configuration, crypto checkout reports itself unavailable; with no email
  transport, verification mail cannot be sent. Missing configuration yields an
  explicit "unavailable" state, never a faked success.
* No demo data, no seeded accounts, no default administrator credentials.
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
