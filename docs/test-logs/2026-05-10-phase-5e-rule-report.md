# Email Triage Evaluation Report

- Generated at: `2026-05-10T17:38:32.036571+08:00`
- Classifier: `rule`
- Provider: `MockEmailProvider`
- Mailbox mutation: `False`
- Sample count: `36`
- Labeled count: `36`

## Metrics

| Metric | Value |
| --- | ---: |
| `action_accuracy` | `1.0` |
| `category_accuracy` | `1.0` |
| `false_negative_count` | `0` |
| `false_positive_count` | `0` |
| `importance_accuracy` | `1.0` |
| `important_precision` | `1.0` |
| `important_recall` | `1.0` |
| `noise_filter_precision` | `1.0` |

## Error Summary

- Error count: `0`

## Mismatch Summary

- Mismatch count: `0`

## Interview Notes

- Important recall tracks whether important mail was missed.
- Important precision tracks whether reported mail was actually important.
- Noise filtering precision tracks whether ignored mail was safe to ignore.
- Category/action mismatches are reviewed separately from reportability errors.
- LLM provider errors are tracked separately from semantic classification errors.
