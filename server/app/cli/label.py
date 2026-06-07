"""Interactive local labeling helpers for the email CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, TextIO

from ..archive_shadow_workflow import proposal_item_id
from ..real_email_eval import save_real_label
from ..real_proposal_eval import save_real_proposal_label
from ..runtime_env import SERVER_ROOT


DEFAULT_REAL_LABEL_PATH = SERVER_ROOT / "data" / "real_email_labels.json"
DEFAULT_REAL_PROPOSAL_LABEL_PATH = SERVER_ROOT / "data" / "real_proposal_labels.json"
InputFunc = Callable[[str], str]


def run_interactive_labeling(args: Any, result: dict[str, Any], *, stdout: TextIO) -> None:
    input_func: InputFunc = getattr(args, "_input_func", input)
    labels_path = result.get("labels_path", str(DEFAULT_REAL_LABEL_PATH))
    saved_count = 0
    skipped_count = 0
    for item in result.get("emails", []):
        if item.get("error"):
            continue
        email = item.get("email", {})
        classification = item.get("classification", {})
        email_id = str(email.get("id", "")).strip()
        if not email_id:
            continue
        while True:
            raw = input_func(f"Label {email_id} [i/l/n/s/q]: ").strip().lower()
            label = interactive_label(raw)
            if label == "quit":
                print(f"Stopped. Saved: {saved_count}, skipped: {skipped_count}", file=stdout)
                return
            if label == "skip":
                skipped_count += 1
                print(f"Skipped {email_id}", file=stdout)
                break
            if label:
                save_real_label(
                    labels_path,
                    email_id=email_id,
                    label=label,
                    subject=email.get("subject", ""),
                    from_email=email.get("from_email", ""),
                    predicted_category=classification.get("category", ""),
                    predicted_importance=classification.get("importance", ""),
                    predicted_action=classification.get("suggested_action", ""),
                    predicted_reportable=bool(classification.get("is_reportable", False)),
                    predicted_ignored=bool(classification.get("is_ignored", False)),
                )
                saved_count += 1
                print(f"Saved {email_id} -> {label}", file=stdout)
                break
            print("Use important/i, later/l, ignore/n, skip/s, or quit/q.", file=stdout)
    print(f"Done. Saved: {saved_count}, skipped: {skipped_count}", file=stdout)


def interactive_label(value: str) -> str:
    mapping = {
        "important": "important",
        "i": "important",
        "later": "later",
        "l": "later",
        "ignore": "ignore",
        "n": "ignore",
        "skip": "skip",
        "s": "skip",
        "quit": "quit",
        "q": "quit",
    }
    return mapping.get(value, "")


def run_interactive_proposal_labeling(args: Any, result: dict[str, Any], *, stdout: TextIO) -> None:
    input_func: InputFunc = getattr(args, "_input_func", input)
    labels_path = result.get("labels_path", str(DEFAULT_REAL_PROPOSAL_LABEL_PATH))
    saved_count = 0
    skipped_count = 0
    for proposal in review_label_items(result):
        item_id = proposal_item_id(proposal)
        if not item_id:
            continue
        while True:
            raw = input_func(f"Label {item_id} [a/k/u/s/q]: ").strip().lower()
            label = interactive_proposal_label(raw)
            if label == "quit":
                print(f"Stopped. Saved: {saved_count}, skipped: {skipped_count}", file=stdout)
                return
            if label == "skip":
                skipped_count += 1
                print(f"Skipped {item_id}", file=stdout)
                break
            if label:
                save_real_proposal_label(labels_path, proposal=proposal, label=label)
                saved_count += 1
                print(f"Saved {item_id} -> {label}", file=stdout)
                break
            print("Use archive/a, keep/k, unsure/u, skip/s, or quit/q.", file=stdout)
    print(f"Done. Saved: {saved_count}, skipped: {skipped_count}", file=stdout)


def interactive_proposal_label(value: str) -> str:
    mapping = {
        "archive": "archive",
        "a": "archive",
        "safe": "archive",
        "yes": "archive",
        "y": "archive",
        "keep": "keep",
        "k": "keep",
        "no": "keep",
        "n": "keep",
        "unsure": "unsure",
        "u": "unsure",
        "unclear": "unsure",
        "skip": "skip",
        "s": "skip",
        "quit": "quit",
        "q": "quit",
    }
    return mapping.get(value, "")


def review_label_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [*list(result.get("proposals", [])), *list(result.get("candidates", []))]
