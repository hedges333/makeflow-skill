#!/usr/bin/env python3
"""Synthetic Scar Candidate Validator CLI entrypoint."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.scar_candidate import (  # noqa: E402
    CandidateArgumentParser,
    ScarCandidateCliError,
    ScarCandidateTestScript,
    ScarCandidateValidator,
    ValidateScarCandidateScript,
    count_words_in_sentence,
    mask_code_and_math_spans,
    split_into_sentences,
)

__all__ = [
    "CandidateArgumentParser",
    "ScarCandidateCliError",
    "ScarCandidateTestScript",
    "ScarCandidateValidator",
    "ValidateScarCandidateScript",
    "count_words_in_sentence",
    "mask_code_and_math_spans",
    "split_into_sentences",
]

if __name__ == "__main__":
  sys.exit(ValidateScarCandidateScript().execute())
