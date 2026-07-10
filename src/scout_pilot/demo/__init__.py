"""Demonstration flows built on generic agent layers."""

from scout_pilot.demo.interview import (
    InterviewDemoResult,
    InterviewDemoSettings,
    LocalDemoServer,
    LocalInterviewSite,
    prepare_local_interview_site,
    run_local_interview_demo,
)
from scout_pilot.demo.vacancy_search import (
    VacancyNote,
    VacancySearchDemoResult,
    VacancySearchDemoRunner,
    VacancySearchDemoSettings,
)

__all__ = [
    "InterviewDemoResult",
    "InterviewDemoSettings",
    "LocalDemoServer",
    "LocalInterviewSite",
    "prepare_local_interview_site",
    "run_local_interview_demo",
    "VacancyNote",
    "VacancySearchDemoResult",
    "VacancySearchDemoRunner",
    "VacancySearchDemoSettings",
]
