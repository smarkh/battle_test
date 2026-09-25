"""Run the plaintiff-vs-defendant document flow from plans/plan.md."""

from dataclasses import dataclass, field
from typing import Callable, Protocol

from battle_test import prompts
from battle_test.config import Config


class ChatClient(Protocol):
    def chat(
        self, model: str, system: str, user: str, on_token: Callable[[str], None] | None = None
    ) -> str: ...


@dataclass(frozen=True)
class Document:
    title: str
    role: str  # "plaintiff" or "defendant"
    text: str
    generated: bool  # False only for a complaint the user supplied


@dataclass
class CaseRun:
    state_code: str
    rounds: int
    plaintiff_model: str
    defendant_model: str
    documents: list[Document] = field(default_factory=list)

    @property
    def state(self) -> str:
        return prompts.SUPPORTED_STATES[self.state_code]


def run_case(
    cfg: Config,
    client: ChatClient,
    state_code: str,
    *,
    facts: str | None = None,
    complaint: str | None = None,
    rounds: int | None = None,
    on_stage: Callable[[str, str], None] | None = None,
    on_token: Callable[[str], None] | None = None,
) -> CaseRun:
    """Produce complaint -> motion -> opposition (-> reply).

    Pass exactly one of `facts` (the complaint is generated from them) or
    `complaint` (a user-written complaint, used as-is).
    """
    if state_code not in prompts.SUPPORTED_STATES:
        supported = ", ".join(prompts.SUPPORTED_STATES)
        raise ValueError(f"Unsupported state {state_code!r}; v1 supports {supported}")
    if (facts is None) == (complaint is None):
        raise ValueError("Provide exactly one of facts or complaint")
    rounds = cfg.rounds if rounds is None else rounds
    if rounds not in (1, 2):
        raise ValueError(f"rounds must be 1 or 2, got {rounds}")

    run = CaseRun(state_code, rounds, cfg.plaintiff_model, cfg.defendant_model)
    state = run.state
    roles = {
        "plaintiff": (cfg.plaintiff_model, prompts.plaintiff_system(state)),
        "defendant": (cfg.defendant_model, prompts.defendant_system(state)),
    }

    def draft(title: str, role: str, task: str) -> str:
        if on_stage:
            on_stage(title, role)
        model, system = roles[role]
        text = client.chat(model, system, task, on_token=on_token).strip()
        run.documents.append(Document(title, role, text, generated=True))
        return text

    if complaint is None:
        complaint = draft("Complaint", "plaintiff", prompts.complaint_task(state, facts))
    else:
        complaint = complaint.strip()
        run.documents.append(Document("Complaint", "plaintiff", complaint, generated=False))

    motion = draft(
        "Motion for Summary Judgment", "plaintiff", prompts.motion_task(state, complaint)
    )
    opposition = draft(
        "Opposition to Motion for Summary Judgment",
        "defendant",
        prompts.opposition_task(state, complaint, motion),
    )
    if rounds == 2:
        draft(
            "Reply in Support of Motion for Summary Judgment",
            "plaintiff",
            prompts.reply_task(state, complaint, motion, opposition),
        )

    return run
