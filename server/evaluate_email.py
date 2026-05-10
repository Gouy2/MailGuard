"""Run mock email evaluation with rule or LLM classifiers."""

from __future__ import annotations

import argparse
import json

from app.email_eval import evaluate_email_classifier
from app.email_eval_report import write_eval_report
from app.email_provider import MockEmailProvider
from app.email_tools import classify_email
from app.llm_email_classifier import LLMEmailClassifier


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate email triage classifiers on labeled mock emails.")
    parser.add_argument("--classifier", choices=["rule", "llm"], default="rule")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--model", default="", help="Optional LLM model override.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request LLM timeout in seconds.")
    parser.add_argument("--max-retries", type=int, default=1, help="Retry count for retryable LLM request failures.")
    parser.add_argument("--report-output", default="", help="Optional path to write a compact evaluation report.")
    parser.add_argument("--report-format", choices=["markdown", "json"], default="markdown")
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Record per-email classifier errors instead of aborting immediately.",
    )
    args = parser.parse_args()

    if args.classifier == "llm":
        llm_classifier = LLMEmailClassifier(
            model=args.model or None,
            timeout=args.timeout,
            max_retries=args.max_retries,
        )
        result = evaluate_email_classifier(
            provider=MockEmailProvider(),
            classifier=llm_classifier.classify,
            limit=args.limit,
            continue_on_error=args.continue_on_error,
        )
        result["classifier"] = "llm_shadow"
        result["model"] = llm_classifier.model
        result["provider"] = "MockEmailProvider"
        result["mailbox_mutation"] = False
    else:
        result = evaluate_email_classifier(
            provider=MockEmailProvider(),
            classifier=classify_email,
            limit=args.limit,
            continue_on_error=args.continue_on_error,
        )
        result["classifier"] = "rule"
        result["provider"] = "MockEmailProvider"
        result["mailbox_mutation"] = False

    if args.report_output:
        report_path = write_eval_report(
            result,
            output_path=args.report_output,
            report_format=args.report_format,
        )
        result["report"] = {
            "format": args.report_format,
            "path": str(report_path),
        }

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
