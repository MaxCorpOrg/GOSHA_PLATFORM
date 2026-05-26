from __future__ import annotations

import unittest

from starlette.middleware.sessions import SessionMiddleware

from oauth_reviewer.app import SESSION_COOKIE_NAME, app


class ReviewerAppSessionCookieTests(unittest.TestCase):
    def test_reviewer_uses_distinct_session_cookie_name(self) -> None:
        session_middlewares = [item for item in app.user_middleware if item.cls is SessionMiddleware]

        self.assertEqual(len(session_middlewares), 1)
        self.assertEqual(session_middlewares[0].kwargs.get("session_cookie"), SESSION_COOKIE_NAME)


if __name__ == "__main__":
    unittest.main()
