"""Speech evaluation: WER plus the fidelity metrics that actually matter here.

Ordinary WER hides the errors that break scheduling. A transcript can score well
while mangling "Cerno", flipping "not" or turning "the eighth" into "the eighteenth".
So this reports, separately:

* WER under a documented normalisation rule
* exact fidelity for project/place names
* exact fidelity for date and time expressions
* negation fidelity ("not", "no", "never", "without", "don't")

Development and held-out splits are kept apart so decode parameters are not
tuned on the evaluation set.

    python -m metis_speech.evaluate --fixtures services/speech/fixtures
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# The normalisation rule, stated once so a score is reproducible: NFKC, casefold,
# strip punctuation except intra-word apostrophes and hyphens, collapse spaces.
_PUNCT = re.compile(r"[^\w\s'\-]", re.UNICODE)
_SPACE = re.compile(r"\s+")

NEGATION_TOKENS = frozenset(
    {"not", "no", "never", "without", "dont", "don't", "cannot", "cant", "isnt", "wasnt"}
)
DATE_PATTERN = re.compile(
    r"\b("
    r"\d{1,2}(?:st|nd|rd|th)?|"
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"today|tomorrow|tonight|yesterday|noon|midnight|am|pm|"
    r"first|second|third|fourth|fifth|eighth|ninth|tenth|twelfth"
    r")\b",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = _PUNCT.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


def tokens(text: str) -> list[str]:
    return normalize(text).split()


def edit_distance(a: list[str], b: list[str]) -> int:
    """Levenshtein over tokens. O(len(a) * len(b)) — fine for short notes.

    ponytail: full DP table avoided; two rows is enough and the inputs are
    sentences, not documents.
    """
    if not a:
        return len(b)
    previous = list(range(len(b) + 1))
    for i, token_a in enumerate(a, start=1):
        current = [i]
        for j, token_b in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,  # deletion
                    current[j - 1] + 1,  # insertion
                    previous[j - 1] + (token_a != token_b),  # substitution
                )
            )
        previous = current
    return previous[-1]


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = tokens(reference), tokens(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(ref, hyp) / len(ref)


def _term_recall(reference: str, hypothesis: str, terms: list[str]) -> tuple[int, int]:
    """How many expected terms survived verbatim (case-insensitively)."""
    hyp = normalize(hypothesis)
    present = [t for t in terms if normalize(t) in hyp]
    return len(present), len(terms)


def _date_recall(reference: str, hypothesis: str) -> tuple[int, int]:
    expected = [m.group(0) for m in DATE_PATTERN.finditer(reference)]
    return _term_recall(reference, hypothesis, expected)


def _negation_recall(reference: str, hypothesis: str) -> tuple[int, int]:
    expected = [t for t in tokens(reference) if t in NEGATION_TOKENS]
    got = [t for t in tokens(hypothesis) if t in NEGATION_TOKENS]
    # Count by multiset intersection: a dropped "not" inverts the meaning.
    kept = 0
    pool = list(got)
    for token in expected:
        if token in pool:
            pool.remove(token)
            kept += 1
    return kept, len(expected)


@dataclass
class Totals:
    count: int = 0
    wer_sum: float = 0.0
    names: tuple[int, int] = (0, 0)
    dates: tuple[int, int] = (0, 0)
    negations: tuple[int, int] = (0, 0)
    per_item: list[dict[str, object]] = field(default_factory=list)

    def add(self, item_id: str, reference: str, hypothesis: str, names: list[str]) -> None:
        item_wer = wer(reference, hypothesis)
        n_ok, n_total = _term_recall(reference, hypothesis, names)
        d_ok, d_total = _date_recall(reference, hypothesis)
        g_ok, g_total = _negation_recall(reference, hypothesis)
        self.count += 1
        self.wer_sum += item_wer
        self.names = (self.names[0] + n_ok, self.names[1] + n_total)
        self.dates = (self.dates[0] + d_ok, self.dates[1] + d_total)
        self.negations = (self.negations[0] + g_ok, self.negations[1] + g_total)
        self.per_item.append(
            {
                "id": item_id,
                "wer": round(item_wer, 4),
                "names": f"{n_ok}/{n_total}",
                "dates": f"{d_ok}/{d_total}",
                "negations": f"{g_ok}/{g_total}",
            }
        )

    def report(self) -> dict[str, object]:
        def ratio(pair: tuple[int, int]) -> float | None:
            return round(pair[0] / pair[1], 4) if pair[1] else None

        return {
            "items": self.count,
            "mean_wer": round(self.wer_sum / self.count, 4) if self.count else None,
            "name_fidelity": ratio(self.names),
            "date_fidelity": ratio(self.dates),
            "negation_fidelity": ratio(self.negations),
            "per_item": self.per_item,
        }


def load_set(path: Path) -> list[dict[str, object]]:
    data = json.loads(path.read_text())
    return list(data["items"])


def evaluate(items: list[dict[str, object]], hypotheses: dict[str, str]) -> dict[str, object]:
    splits: dict[str, Totals] = {"dev": Totals(), "heldout": Totals()}
    missing: list[str] = []
    for item in items:
        item_id = str(item["id"])
        hypothesis = hypotheses.get(item_id)
        if hypothesis is None:
            missing.append(item_id)
            continue
        split = str(item.get("split", "dev"))
        splits.setdefault(split, Totals()).add(
            item_id,
            str(item["reference"]),
            hypothesis,
            [str(n) for n in item.get("names", [])],
        )
    return {
        "splits": {name: totals.report() for name, totals in splits.items() if totals.count},
        "missing_hypotheses": missing,
        "normalization": "NFKC, casefold, strip punctuation except ' and -, collapse whitespace",
        "caveat": (
            "A pilot set of this size cannot support a universal accuracy claim. "
            "Report these numbers with the machine, model artifact and decode parameters used."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Metis speech evaluation")
    parser.add_argument("--fixtures", default="services/speech/fixtures")
    parser.add_argument(
        "--hypotheses",
        help="JSON mapping {item_id: transcript}. Omitted: the fixture transcriber is used.",
    )
    parser.add_argument("--json", action="store_true", help="print the raw report")
    args = parser.parse_args(argv)

    fixtures = Path(args.fixtures)
    eval_file = fixtures / "evaluation_set.json"
    if not eval_file.is_file():
        print(f"no evaluation set at {eval_file}")
        return 2
    items = load_set(eval_file)

    if args.hypotheses:
        hypotheses = json.loads(Path(args.hypotheses).read_text())
    else:
        # No audio in the repo, so the fixture reference is echoed back. This
        # exercises the metric code; it is NOT a measurement of the real decoder.
        hypotheses = {str(i["id"]): str(i["reference"]) for i in items}
        print("WARNING: no --hypotheses given; echoing references. This measures nothing about ASR accuracy.")

    report = evaluate(items, hypotheses)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    for split, totals in report["splits"].items():  # type: ignore[union-attr]
        print(f"\n[{split}] items={totals['items']} mean_wer={totals['mean_wer']}")
        print(
            f"  name_fidelity={totals['name_fidelity']} "
            f"date_fidelity={totals['date_fidelity']} "
            f"negation_fidelity={totals['negation_fidelity']}"
        )
    if report["missing_hypotheses"]:
        print(f"\nmissing hypotheses: {report['missing_hypotheses']}")
    print(f"\nnormalization: {report['normalization']}")
    print(f"caveat: {report['caveat']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
