"""Example entry point for the team-roster example feature `price-monitor`.

Minimal, honest example: checks prices and discounts and reports only
when something changed. The real implementation lives wherever the
team's own monitor is; this file exists so the shipped example tree
(`examples/starter-repo/`) passes the team-roster checks out of the
box — the registry records `entry: scripts/monitor_harga.py:cek_harga`
and the hook verifies the file and the symbol exist.
"""


def cek_harga():
    """Check prices + discounts, return a list of changes.

    Returns [] when nothing changed — the caller reports only on change.
    """
    # Example stub: a real implementation would fetch the product pages
    # and compare against the last recorded prices.
    return []
