"""Thin adapter over LeetCode's GraphQL endpoint.

LeetCode does not publish this endpoint as a supported public API (see
ARCHITECTURE.md). Every GraphQL query/field name lives in this one file on
purpose, so a breaking upstream change is a one-file fix.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from src.utils.retry import retry

log = logging.getLogger(__name__)

GRAPHQL_ENDPOINT = "https://leetcode.com/graphql"

_DAILY_QUERY = """
query questionOfToday {
  activeDailyCodingChallengeQuestion {
    date
    link
    question {
      questionFrontendId
      title
      titleSlug
      difficulty
    }
  }
}
"""

_QUESTION_QUERY = """
query questionData($titleSlug: String!) {
  question(titleSlug: $titleSlug) {
    questionFrontendId
    title
    titleSlug
    content
    difficulty
    exampleTestcases
    topicTags {
      name
    }
    codeSnippets {
      langSlug
      code
    }
    isPaidOnly
  }
}
"""


class LeetCodeError(RuntimeError):
    """Raised for any LeetCode fetch failure the caller should treat as fatal
    for this run (after retries are exhausted)."""


class PremiumProblemError(LeetCodeError):
    """Raised when the target problem is premium-only. The pipeline's
    configured policy (config/settings.yaml leetcode.premium_policy) decides
    whether this stops the run — default is "skip", never attempt to bypass
    the paywall."""


@dataclass
class RawQuestion:
    """Unparsed GraphQL response for a single question, before parser.py
    normalizes it into src.leetcode.models.Problem."""

    date: str
    frontend_id: str
    title: str
    slug: str
    difficulty: str
    content_html: str
    topics: list[str]
    example_testcases: str
    python_template: str
    is_premium: bool


class LeetCodeClient:
    def __init__(self, timeout_seconds: int = 15, max_retries: int = 3):
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Content-Type": "application/json",
                "Referer": "https://leetcode.com",
                "User-Agent": (
                    "leetcode-daily-cheatsheet/0.1 "
                    "(personal automation; https://github.com/)"
                ),
            }
        )

    def _post(self, query: str, variables: dict | None = None) -> dict:
        @retry(
            exceptions=(requests.RequestException, LeetCodeError),
            attempts=self.max_retries,
        )
        def _do() -> dict:
            resp = self._session.post(
                GRAPHQL_ENDPOINT,
                json={"query": query, "variables": variables or {}},
                timeout=self.timeout_seconds,
            )
            if resp.status_code >= 500:
                raise LeetCodeError(f"LeetCode returned {resp.status_code}, retrying")
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("errors"):
                raise LeetCodeError(f"GraphQL errors: {payload['errors']}")
            return payload["data"]

        return _do()

    def fetch_daily_slug(self) -> tuple[str, str]:
        """Returns (date, titleSlug) for today's Daily Challenge."""
        data = self._post(_DAILY_QUERY)
        challenge = data.get("activeDailyCodingChallengeQuestion")
        if not challenge:
            raise LeetCodeError("No activeDailyCodingChallengeQuestion in response")
        return challenge["date"], challenge["question"]["titleSlug"]

    def fetch_question(self, title_slug: str) -> RawQuestion:
        data = self._post(_QUESTION_QUERY, {"titleSlug": title_slug})
        q = data.get("question")
        if not q:
            raise LeetCodeError(f"No question found for slug '{title_slug}'")

        if q.get("isPaidOnly"):
            raise PremiumProblemError(
                f"'{title_slug}' is premium-only; refusing to fetch further "
                "(leetcode.premium_policy = skip)"
            )

        python_template = ""
        for snippet in q.get("codeSnippets") or []:
            if snippet.get("langSlug") == "python3":
                python_template = snippet.get("code", "")
                break

        return RawQuestion(
            date="",  # filled by caller for the daily-challenge path
            frontend_id=q["questionFrontendId"],
            title=q["title"],
            slug=q["titleSlug"],
            difficulty=q["difficulty"],
            content_html=q.get("content") or "",
            topics=[t["name"] for t in (q.get("topicTags") or [])],
            example_testcases=q.get("exampleTestcases") or "",
            python_template=python_template,
            is_premium=bool(q.get("isPaidOnly")),
        )

    def fetch_daily(self) -> RawQuestion:
        date, slug = self.fetch_daily_slug()
        question = self.fetch_question(slug)
        question.date = date
        return question

    def fetch_by_slug(self, title_slug: str, date: str) -> RawQuestion:
        question = self.fetch_question(title_slug)
        question.date = date
        return question
