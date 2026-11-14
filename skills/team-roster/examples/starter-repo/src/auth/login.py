"""Example entry point for the team-roster example feature `login-api`.

This is a minimal, honest example — not a real auth system. It exists
so the shipped example tree (`examples/starter-repo/`) passes the
team-roster checks out of the box: the feature registry records
`entry: src/auth/login.py:login` and the hook verifies the file and the
symbol exist, and that the blob hash matches `entry_blob_sha`.
"""


def login(email, password):
    """Log in with email + password, return a session token.

    Raises ValueError on empty credentials. The "token" is a plain
    string for the example — a real implementation would check a
    credential store and issue a signed token.
    """
    if not email or not password:
        raise ValueError("email and password are both required")
    return {"email": email, "token": "session-%s" % email.split("@")[0]}
