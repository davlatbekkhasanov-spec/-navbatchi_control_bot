"""Yordamchi hub uchun kunlik xulosa matni."""

from __future__ import annotations


def compact_hub_summary(
    *,
    score: int,
    status: str,
    before: int = 0,
    after: int = 0,
) -> str:
    st = (status or "unknown").strip().lower()
    return (
        f"Navbatchi: ball={int(score)}, status {st}, "
        f"before {int(before)}, after {int(after)}"
    )
