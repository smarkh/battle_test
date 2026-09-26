"""A stand-in model for developing the web UI without a GPU.

Research, selection and citation checking still run for real against the
law index. Only the drafting is canned: each draft cites the first
authority it was given (so a ✅ appears), one invented section (so a ❌
appears), and leaves one placeholder, streamed a word at a time.
"""

import json
import re
import time
from typing import Callable

_FIRST_AUTHORITY = re.compile(r"=== AUTHORITIES ===\n(?P<citation>[^\n—]+?) —")
_TASK_TITLE = re.compile(r"^Draft (?:a |the )?(?P<what>[^,.]+)", re.MULTILINE)


class DemoClient:
    def __init__(self, delay_per_word: float = 0.02):
        self.delay = delay_per_word

    def chat(self, model: str, system: str, user: str,
             on_token: Callable[[str], None] | None = None, json_mode: bool = False) -> str:
        if json_mode:
            if '"queries"' in user:
                return json.dumps({"queries": ["breach of contract damages", "limitation of actions contract"]})
            return json.dumps({"selected": [1, 2, 3]})

        authority = _FIRST_AUTHORITY.search(user)
        cited = authority.group("citation").strip() if authority else "no authority was provided"
        what = _TASK_TITLE.search(user)
        text = (
            f"**DEMO DRAFT: {what.group('what').strip().upper() if what else 'DOCUMENT'}**\n\n"
            "This text comes from the demo model, not a real one. It exists to exercise the web UI.\n\n"
            f"1. The claim is governed by {cited}, which was quoted in the prompt.\n"
            "2. The demo also cites Utah Code § 99-99-999, which does not exist, so the checker flags it.\n"
            "3. Some authority is still missing. [CITATION NEEDED: an authority the index didn't supply]\n"
        )
        for word in re.split(r"(\s+)", text):
            if on_token and word:
                on_token(word)
            if self.delay and word.strip():
                time.sleep(self.delay)
        return text
