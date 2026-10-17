"""Job matching.

  contract.py  JobFilter — what adapters call
  default.py   DefaultJobFilter — current title + experience rules
  titles.py    token overlap and title include/exclude
  experience.py  years-of-experience exclude
  filters.py   compiles regexes from a portal's [filters] table

To tighten or loosen roles: edit [filters] in config/portals/<name>.toml.
To change the algorithm: edit titles.py / experience.py (or replace DefaultJobFilter).
"""
from .contract import JobFilter
from .default import DefaultJobFilter
from .experience import experience_skip_reason
from .filters import ExperienceFilters, TitleFilters, compile_patterns
from .titles import title_skip_reason, titles_close, words

__all__ = [
    "DefaultJobFilter",
    "ExperienceFilters",
    "JobFilter",
    "TitleFilters",
    "compile_patterns",
    "experience_skip_reason",
    "title_skip_reason",
    "titles_close",
    "words",
]
