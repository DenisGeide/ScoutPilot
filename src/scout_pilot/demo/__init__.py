"""Demonstration flows built on generic agent layers."""

from scout_pilot.demo.interview import (
    InterviewDemoResult,
    InterviewDemoSettings,
    LocalDemoServer,
    LocalInterviewSite,
    prepare_local_interview_site,
    run_local_interview_demo,
)
from scout_pilot.demo.live_local import (
    DEFAULT_LIVE_LOCAL_TASK,
    LiveLocalDemoResult,
    LiveLocalDemoSettings,
    LocalLiveRuntimeSite,
    prepare_live_local_demo_site,
    run_live_local_demo,
)
from scout_pilot.demo.mail_spam import (
    DEFAULT_MAIL_SPAM_TASK,
    LocalMailSite,
    MailMessageNote,
    MailSpamDemoResult,
    MailSpamDemoSettings,
    prepare_local_mail_site,
    run_local_mail_spam_demo,
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
    "DEFAULT_LIVE_LOCAL_TASK",
    "DEFAULT_MAIL_SPAM_TASK",
    "LiveLocalDemoResult",
    "LiveLocalDemoSettings",
    "LocalDemoServer",
    "LocalInterviewSite",
    "LocalLiveRuntimeSite",
    "LocalMailSite",
    "MailMessageNote",
    "MailSpamDemoResult",
    "MailSpamDemoSettings",
    "prepare_local_interview_site",
    "prepare_live_local_demo_site",
    "prepare_local_mail_site",
    "run_local_interview_demo",
    "run_live_local_demo",
    "run_local_mail_spam_demo",
    "VacancyNote",
    "VacancySearchDemoResult",
    "VacancySearchDemoRunner",
    "VacancySearchDemoSettings",
]
