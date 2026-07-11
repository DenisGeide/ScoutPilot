"""Synthetic mail spam demonstration built on generic browser-agent layers."""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from scout_pilot.browser import BrowserEngineConfig, PlaywrightBrowserEngine
from scout_pilot.config import AppConfig
from scout_pilot.context import DeterministicContextBudgeter
from scout_pilot.demo.interview import LocalDemoServer
from scout_pilot.models import InteractiveElement, PageIssueCode, PageObservation, ToolRequest
from scout_pilot.observation import ObservationSettings, SemanticObservationEngine
from scout_pilot.reporting import DemoReportRecorder
from scout_pilot.tools import (
    DefaultToolRuntime,
    ToolContext,
    ToolExecutionResult,
    ToolExecutionStatus,
    create_browser_tool_registry,
)


ProgressCallback = Callable[[str], None]

DEFAULT_MAIL_SPAM_TASK = (
    "Прочитай последние 10 писем и определи спам. Остановись перед удалением."
)

_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+|\n+")
_EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", re.IGNORECASE)
_MESSAGE_LIMIT = 10
_PHISHING_TERMS = (
    "account suspended",
    "bank",
    "confirm your password",
    "crypto",
    "delete",
    "gift card",
    "limited time",
    "login",
    "lottery",
    "move to spam",
    "overdue",
    "password",
    "prize",
    "security alert",
    "urgent",
    "verify",
    "wallet",
    "winner",
    "выигрыш",
    "пароль",
    "подтверд",
    "срочно",
)
_PROMO_TERMS = (
    "discount",
    "newsletter",
    "promotion",
    "sale",
    "special offer",
    "webinar",
)
_HARD_BLOCKER_CODES = {
    PageIssueCode.CAPTCHA_BLOCKING_PAGE.value,
    PageIssueCode.LOGIN_WALL.value,
    PageIssueCode.EMPTY_PAGE.value,
    PageIssueCode.NAVIGATION_ERROR.value,
    PageIssueCode.OBSERVATION_ERROR.value,
}


@dataclass(frozen=True)
class MailSpamDemoSettings:
    """Settings for the deterministic synthetic mail demo."""

    site_dir: Path = Path("reports/tmp/mail-spam-demo-site")
    profile_dir: Path = Path(".browser-profiles/mail-spam-demo")
    report_path: Path = Path("reports/tmp/mail-spam-demo-report.json")
    replay_path: Path = Path("reports/tmp/mail-spam-demo-replay.json")
    headless: bool = False
    slow_mo_ms: int = 80
    max_messages: int = _MESSAGE_LIMIT

    def __post_init__(self) -> None:
        if self.slow_mo_ms < 0:
            raise ValueError("slow_mo_ms cannot be negative")
        if self.max_messages <= 0:
            raise ValueError("max_messages must be positive")
        if self.max_messages > _MESSAGE_LIMIT:
            raise ValueError(f"max_messages cannot exceed {_MESSAGE_LIMIT}")


@dataclass(frozen=True)
class MailMessageNote:
    """Short safe note about one synthetic message."""

    subject: str
    sender: str | None
    url: str | None
    classification: str
    reasons: tuple[str, ...]
    summary: str

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "subject": self.subject,
            "sender": self.sender,
            "url": self.url,
            "classification": self.classification,
            "reasons": list(self.reasons),
            "summary": self.summary,
        }


@dataclass(frozen=True)
class MailSpamDemoResult:
    """Outcome returned by the synthetic mail demo."""

    success: bool
    message_ru: str
    local_site_url: str
    report_path: Path
    replay_path: Path
    messages_read: int
    spam_candidates: int
    security_pause_count: int


@dataclass(frozen=True)
class LocalMailSite:
    """Generated synthetic mail site metadata."""

    root: Path
    start_page_name: str = "index.html"


@dataclass(frozen=True)
class _SyntheticMailMessage:
    file_name: str
    sender: str
    subject: str
    preview: str
    body: str


async def run_local_mail_spam_demo(
    config: AppConfig,
    settings: MailSpamDemoSettings,
    *,
    progress: ProgressCallback | None = None,
) -> MailSpamDemoResult:
    """Run the deterministic local mail demo without real accounts or providers."""

    site = prepare_local_mail_site(settings.site_dir)

    def emit(message_ru: str) -> None:
        if progress is not None:
            progress(message_ru)

    emit("Готовлю локальный синтетический почтовый сайт.")
    emit(
        f"Постоянный профиль браузера настроен: {settings.profile_dir}. "
        "Путь исключен из Git."
    )

    with LocalDemoServer(site.root) as server:
        start_url = server.url_for(site.start_page_name)
        report = DemoReportRecorder(
            demo_name="synthetic_mail_spam",
            task=DEFAULT_MAIL_SPAM_TASK,
            start_url=start_url,
        )
        context_budgeter = DeterministicContextBudgeter()
        notes: list[MailMessageNote] = []

        browser_settings = replace(
            BrowserEngineConfig.from_app_config(config),
            user_data_dir=settings.profile_dir,
            headless=settings.headless,
            slow_mo_ms=settings.slow_mo_ms,
        )
        browser = PlaywrightBrowserEngine(browser_settings)
        base_observation_settings = ObservationSettings.from_app_config(config)
        observation_engine = SemanticObservationEngine(
            browser,
            replace(
                base_observation_settings,
                max_sections=max(base_observation_settings.max_sections, 32),
                max_interactive_elements=max(
                    base_observation_settings.max_interactive_elements,
                    80,
                ),
                max_total_chars=max(base_observation_settings.max_total_chars, 20000),
            ),
        )
        tool_runtime = DefaultToolRuntime(
            create_browser_tool_registry(),
            ToolContext(browser=browser, observation_engine=observation_engine),
        )

        try:
            await browser.start()
            emit("Открываю синтетический inbox.")
            navigation = await _execute(
                tool_runtime,
                ToolRequest("browser.navigate", {"url": start_url}),
                report=report,
                phase="open_inbox",
            )
            if not navigation.success:
                return _final_result(
                    report,
                    settings,
                    start_url,
                    notes,
                    False,
                    "Не удалось открыть локальный inbox. Подробности записаны в отчет.",
                )

            inbox_observation = await _observe(
                observation_engine,
                context_budgeter,
                report,
                phase="inbox",
            )
            if _has_blocking_issue(inbox_observation):
                return _final_result(
                    report,
                    settings,
                    start_url,
                    notes,
                    False,
                    "Inbox выглядит недоступным или пустым. Демо остановлено.",
                )

            message_links = _message_links(inbox_observation, settings.max_messages)
            report.record_discovered_urls(tuple(link.target_url for link in message_links))
            emit(f"Нашел {len(message_links)} писем в локальном inbox.")
            if len(message_links) < settings.max_messages:
                return _final_result(
                    report,
                    settings,
                    start_url,
                    notes,
                    False,
                    "Локальный inbox не содержит ожидаемые 10 писем.",
                )

            for index, link in enumerate(message_links, start=1):
                target_url = link.target_url
                if target_url is None:
                    continue
                emit(f"Читаю письмо {index}/{settings.max_messages}.")
                report.record_event(
                    "decision",
                    phase="read_message",
                    message=(
                        "Open a message through the discovered semantic link URL; "
                        "do not use real mail accounts or provider APIs."
                    ),
                    link_text=link.accessible_name or link.visible_text,
                )
                result = await _execute(
                    tool_runtime,
                    ToolRequest("browser.navigate", {"url": target_url}),
                    report=report,
                    phase=f"open_message_{index}",
                )
                if not result.success:
                    report.record_blocker(
                        phase=f"open_message_{index}",
                        url=target_url,
                        title=link.accessible_name or link.visible_text,
                        issues=[{"code": result.error_code or "navigation_failed"}],
                    )
                    continue

                observation = await _observe(
                    observation_engine,
                    context_budgeter,
                    report,
                    phase=f"message_{index}",
                )
                note = _classify_message(observation, target_url)
                notes.append(note)
                report.record_page_read(
                    index=index,
                    title=note.subject,
                    url=target_url,
                    classification=note.classification,
                    reasons=note.reasons,
                )
                report.record_note(note.to_dict())

            spam_notes = [note for note in notes if note.classification == "likely_spam"]
            emit(f"Определил вероятный спам: {len(spam_notes)}.")
            if spam_notes:
                first_spam = spam_notes[0]
                if first_spam.url is not None:
                    await _execute(
                        tool_runtime,
                        ToolRequest("browser.navigate", {"url": first_spam.url}),
                        report=report,
                        phase="prepare_spam_action",
                    )
                    paused = await _execute(
                        tool_runtime,
                        ToolRequest(
                            "browser.click_by_intent",
                            {
                                "target": "Move to spam",
                                "role": "button",
                                "context": first_spam.subject,
                            },
                        ),
                        report=report,
                        phase="probe_spam_safety",
                    )
                    if paused.status is ToolExecutionStatus.PAUSED:
                        emit(
                            "Остановился перед удалением или переносом письма в спам. "
                            "Security Policy требует подтверждение."
                        )
                    else:
                        report.record_event(
                            "security_probe_result",
                            status=paused.status.value,
                            success=paused.success,
                            message=paused.message,
                        )

            success = len(notes) == settings.max_messages and len(report.security_pauses) >= 1
            message_ru = _final_summary_ru(notes, len(report.security_pauses))
            return _final_result(report, settings, start_url, notes, success, message_ru)
        finally:
            await browser.stop()


def prepare_local_mail_site(root: Path) -> LocalMailSite:
    """Create deterministic synthetic mail pages for the spam demo."""

    root.mkdir(parents=True, exist_ok=True)
    messages = _synthetic_messages()
    for index, message in enumerate(messages, start=1):
        _write_message_page(root / message.file_name, index, message)
    _write_inbox_page(root / "index.html", messages)
    return LocalMailSite(root=root)


def _synthetic_messages() -> tuple[_SyntheticMailMessage, ...]:
    return (
        _SyntheticMailMessage(
            file_name="message-01.html",
            sender="team@project.test",
            subject="Sprint planning notes",
            preview="Agenda and owners for the next planning session.",
            body=(
                "The team will review the delivery board, agree on owners, and move two "
                "research tasks into the next sprint."
            ),
        ),
        _SyntheticMailMessage(
            file_name="message-02.html",
            sender="security-alert@notice.test",
            subject="Urgent password verification required",
            preview="The account may be suspended unless the password is confirmed.",
            body=(
                "Security alert: confirm your password today to keep the account active. "
                "The link asks for login data outside the mail site."
            ),
        ),
        _SyntheticMailMessage(
            file_name="message-03.html",
            sender="newsletter@learning.test",
            subject="Python AI webinar next week",
            preview="Invitation to a technical webinar about AI tooling.",
            body=(
                "This newsletter announces a Python AI webinar with a discount for early "
                "registration. No personal data is requested."
            ),
        ),
        _SyntheticMailMessage(
            file_name="message-04.html",
            sender="finance@project.test",
            subject="Monthly budget summary",
            preview="Internal synthetic report with project budget numbers.",
            body=(
                "The monthly summary lists planned expenses for test infrastructure and "
                "does not ask the recipient to open external payment pages."
            ),
        ),
        _SyntheticMailMessage(
            file_name="message-05.html",
            sender="winner@promo.test",
            subject="Prize winner confirmation",
            preview="Claims a prize and asks to confirm payment information.",
            body=(
                "You are the winner of a special prize. Verify bank details and login "
                "information before the limited time offer expires."
            ),
        ),
        _SyntheticMailMessage(
            file_name="message-06.html",
            sender="hr@project.test",
            subject="Interview schedule",
            preview="A synthetic calendar note for a candidate interview.",
            body=(
                "The interview starts at 11:00. Please review the prepared questions and "
                "join the regular internal meeting room."
            ),
        ),
        _SyntheticMailMessage(
            file_name="message-07.html",
            sender="offers@tools.test",
            subject="Developer tools discount",
            preview="Promotional discount for a developer tool subscription.",
            body=(
                "This promotion offers a discount on a developer tool subscription. It is "
                "marketing content and does not require account credentials."
            ),
        ),
        _SyntheticMailMessage(
            file_name="message-08.html",
            sender="wallet-check@crypto.test",
            subject="Crypto wallet access limited",
            preview="Says a wallet will be blocked without urgent verification.",
            body=(
                "Your crypto wallet access is limited. Verify login and password details "
                "through a suspicious external page to unlock funds."
            ),
        ),
        _SyntheticMailMessage(
            file_name="message-09.html",
            sender="ops@project.test",
            subject="Deployment window moved",
            preview="Synthetic operations notice about a deployment time change.",
            body=(
                "The deployment window moved to Thursday. No destructive action is needed; "
                "the note only changes the team calendar."
            ),
        ),
        _SyntheticMailMessage(
            file_name="message-10.html",
            sender="billing-alert@notice.test",
            subject="Overdue invoice and account deletion warning",
            preview="Threatens deletion and asks for immediate payment verification.",
            body=(
                "The invoice is overdue and the account will be deleted unless payment and "
                "bank details are verified immediately."
            ),
        ),
    )


def _write_inbox_page(root_page: Path, messages: tuple[_SyntheticMailMessage, ...]) -> None:
    rows = "\n".join(
        (
            f'<article class="message-card" aria-label="Message {index}: '
            f'{html.escape(message.subject)}">'
            f"<h2>{html.escape(message.subject)}</h2>"
            f'<a href="{html.escape(message.file_name)}">'
            f"Open message {index}: {html.escape(message.subject)}"
            f"</a>"
            f"<p><strong>From:</strong> {html.escape(message.sender)}</p>"
            f"<p>{html.escape(message.preview)}</p>"
            f"</article>"
        )
        for index, message in enumerate(messages, start=1)
    )
    root_page.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Synthetic Mail Inbox</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; color: #1f2933; }}
    header, main {{ padding: 10px 18px; }}
    header {{ background: #edf4f8; border-bottom: 1px solid #cdd9e1; }}
    header h1 {{ margin: 0 0 4px; font-size: 22px; }}
    header p {{ margin: 0; }}
    .message-card {{
      border-top: 1px solid #d8e0e7;
      display: grid;
      grid-template-columns: 1.1fr 1.2fr 1fr 2fr;
      gap: 10px;
      align-items: center;
      padding: 6px 0;
    }}
    .message-card h2 {{ font-size: 14px; margin: 0; }}
    .message-card p {{ font-size: 12px; margin: 0; }}
    a, button {{ font: inherit; }}
    button {{ margin-right: 8px; padding: 8px 12px; }}
  </style>
</head>
<body>
  <header>
    <h1>Inbox</h1>
    <p>Local synthetic mailbox. No real account, no network mail provider.</p>
  </header>
  <main aria-label="Inbox messages">
    {rows}
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )


def _write_message_page(path: Path, index: int, message: _SyntheticMailMessage) -> None:
    path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(message.subject)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; color: #1f2933; }}
    header, main {{ padding: 24px 32px; }}
    header {{ background: #f5f1ea; border-bottom: 1px solid #ded3c0; }}
    .actions {{ margin-top: 28px; }}
    button {{ margin-right: 8px; padding: 8px 12px; }}
  </style>
</head>
<body>
  <header>
    <a href="index.html">Back to inbox</a>
    <h1>{html.escape(message.subject)}</h1>
  </header>
  <main aria-label="Message detail">
    <p><strong>Message:</strong> {index}</p>
    <p><strong>From:</strong> {html.escape(message.sender)}</p>
    <p><strong>Subject:</strong> {html.escape(message.subject)}</p>
    <section aria-label="Message body">
      <p>{html.escape(message.body)}</p>
    </section>
    <section class="actions" aria-label="Mail actions">
      <button type="button" aria-label="Move to spam">Move to spam</button>
      <button type="button" aria-label="Delete message">Delete message</button>
      <button type="button" aria-label="Keep message">Keep message</button>
    </section>
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )


async def _observe(
    observation_engine: SemanticObservationEngine,
    context_budgeter: DeterministicContextBudgeter,
    report: DemoReportRecorder,
    *,
    phase: str,
) -> PageObservation:
    observation = await observation_engine.observe()
    report.record_event(
        "observation",
        phase=phase,
        observation=_observation_to_report(observation),
    )
    if _has_blocking_issue(observation):
        report.record_blocker(
            phase=phase,
            url=observation.url,
            title=observation.title,
            issues=[
                {
                    "code": issue.code.value,
                    "message": issue.message,
                    "severity": issue.severity,
                }
                for issue in observation.issues
            ],
        )
    budgeted = context_budgeter.assemble(
        user_task=DEFAULT_MAIL_SPAM_TASK,
        observation=observation,
        memory_summaries=(
            "task.user_goal: read 10 synthetic messages and identify likely spam.",
            "constraint: do not connect to real email providers.",
            "security: confirmation is required before deleting or moving mail.",
        ),
    )
    report.record_event(
        "context_budget",
        phase=phase,
        metrics=budgeted.metrics.to_dict(),
        budget=dict(budgeted.budget),
    )
    return observation


async def _execute(
    tool_runtime: DefaultToolRuntime,
    request: ToolRequest,
    *,
    report: DemoReportRecorder,
    phase: str,
) -> ToolExecutionResult:
    report.record_event(
        "selected_tool",
        phase=phase,
        tool_name=request.name,
        arguments=_redact_tool_arguments(request.arguments),
    )
    result = await tool_runtime.execute(request)
    report.record_event(
        "tool_result",
        phase=phase,
        tool_name=request.name,
        status=result.status.value,
        success=result.success,
        message=result.message,
        error_code=result.error_code,
        retryable=result.retryable,
        data=_tool_data_to_report(result.data),
    )
    if result.status is ToolExecutionStatus.PAUSED:
        confirmation = _confirmation_from_result(result)
        report.record_security_pause(
            phase=phase,
            tool_name=request.name,
            message_ru=str(confirmation.get("message_ru") or result.message),
            risk=_nested_value(result.data, "security", "risk"),
            confirmation_id=confirmation.get("confirmation_id"),
            action=confirmation.get("action"),
            expected_consequence=confirmation.get("expected_consequence"),
        )
    return result


def _final_result(
    report: DemoReportRecorder,
    settings: MailSpamDemoSettings,
    local_site_url: str,
    notes: list[MailMessageNote],
    success: bool,
    message_ru: str,
) -> MailSpamDemoResult:
    report.set_final(
        success=success,
        stop_reason=("completed_with_security_pause" if success else "failed"),
        summary_ru=message_ru,
    )
    report_path = report.write(settings.report_path)
    replay_path = report.write_replay(settings.replay_path)
    return MailSpamDemoResult(
        success=success,
        message_ru=message_ru,
        local_site_url=local_site_url,
        report_path=report_path,
        replay_path=replay_path,
        messages_read=len(notes),
        spam_candidates=sum(1 for note in notes if note.classification == "likely_spam"),
        security_pause_count=len(report.security_pauses),
    )


def _message_links(
    observation: PageObservation,
    limit: int,
) -> tuple[InteractiveElement, ...]:
    candidates: list[InteractiveElement] = []
    seen_urls: set[str] = set()
    for element in observation.interactive_elements:
        if element.role.casefold() != "link" or not element.target_url:
            continue
        text = _element_text(element).casefold()
        if "message-" not in element.target_url.casefold() and "open message" not in text:
            continue
        if element.target_url in seen_urls:
            continue
        candidates.append(element)
        seen_urls.add(element.target_url)
    return tuple(candidates[:limit])


def _classify_message(observation: PageObservation, url: str | None) -> MailMessageNote:
    text = _observation_text(observation)
    lowered = text.casefold()
    reasons: list[str] = []
    for term in _PHISHING_TERMS:
        if term in lowered:
            reasons.append(f"contains risk term: {term}")
        if len(reasons) >= 4:
            break
    promo = any(term in lowered for term in _PROMO_TERMS)
    if reasons:
        classification = "likely_spam"
    elif promo:
        classification = "promotional"
        reasons.append("marketing or newsletter wording")
    else:
        classification = "normal"
        reasons.append("no urgent credential, payment or deletion request detected")

    return MailMessageNote(
        subject=observation.title or _extract_subject(text) or "Untitled message",
        sender=_extract_sender(text),
        url=url,
        classification=classification,
        reasons=tuple(dict.fromkeys(reasons)),
        summary=_safe_message_summary(text),
    )


def _observation_text(observation: PageObservation) -> str:
    parts = [observation.title or "", observation.summary or ""]
    parts.extend(section.text for section in observation.sections)
    parts.extend(
        part
        for element in observation.interactive_elements
        for part in (element.accessible_name, element.visible_text)
        if part
    )
    return " ".join(" ".join(part.split()) for part in parts if part)


def _extract_sender(text: str) -> str | None:
    match = _EMAIL_PATTERN.search(text)
    return match.group(0) if match else None


def _extract_subject(text: str) -> str | None:
    match = re.search(r"Subject:\s+([^.]*)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return _truncate_text(match.group(1), 120)


def _safe_message_summary(text: str) -> str:
    chunks = [
        " ".join(chunk.split())
        for chunk in _SENTENCE_SPLIT_PATTERN.split(text)
        if chunk.strip()
    ]
    useful = [
        chunk
        for chunk in chunks
        if "move to spam" not in chunk.casefold()
        and "delete message" not in chunk.casefold()
        and "keep message" not in chunk.casefold()
    ]
    return _truncate_text(" ".join(useful[:2]) if useful else text, 260)


def _final_summary_ru(notes: list[MailMessageNote], pauses: int) -> str:
    spam_subjects = [note.subject for note in notes if note.classification == "likely_spam"]
    if spam_subjects:
        spam_text = "; ".join(spam_subjects)
    else:
        spam_text = "явных кандидатов не найдено"
    return (
        f"Демо прочитало {len(notes)} синтетических писем, отметило вероятный спам: "
        f"{spam_text}. Удаление и перенос в спам не выполнялись; "
        f"пауз безопасности: {pauses}."
    )


def _observation_to_report(observation: PageObservation) -> Mapping[str, Any]:
    return {
        "url": observation.url,
        "title": observation.title,
        "summary": observation.summary,
        "issues": [
            {
                "code": issue.code.value,
                "message": issue.message,
                "severity": issue.severity,
            }
            for issue in observation.issues
        ],
        "sections": [
            {
                "id": section.section_id,
                "role": section.role,
                "heading": section.heading,
                "text": _truncate_text(section.text, 360),
            }
            for section in observation.sections[:8]
        ],
        "interactive_elements": [
            {
                "id": element.element_id,
                "role": element.role,
                "accessible_name": element.accessible_name,
                "visible_text": element.visible_text,
                "target_url": element.target_url,
                "input_type": element.input_type,
            }
            for element in observation.interactive_elements[:18]
        ],
    }


def _tool_data_to_report(data: Mapping[str, Any]) -> Mapping[str, Any]:
    allowed_keys = {
        "action",
        "url",
        "title",
        "resolution",
        "transition",
        "recovered_from_stale",
        "security",
        "confirmation",
    }
    return {
        key: _sanitize_report_value(value)
        for key, value in data.items()
        if key in allowed_keys
    }


def _sanitize_report_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_report_value(item)
            for key, item in value.items()
            if str(key).casefold() not in {"request_signature"}
        }
    if isinstance(value, tuple | list):
        return [_sanitize_report_value(item) for item in value]
    if isinstance(value, str):
        return _truncate_text(value, 800)
    return value


def _redact_tool_arguments(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in arguments.items():
        if key.casefold() in {"value", "password", "token", "secret"}:
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = value
    return redacted


def _confirmation_from_result(result: ToolExecutionResult) -> Mapping[str, Any]:
    raw = result.data.get("confirmation")
    return raw if isinstance(raw, Mapping) else {}


def _nested_value(data: Mapping[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _has_blocking_issue(observation: PageObservation) -> bool:
    codes = {issue.code.value for issue in observation.issues}
    if PageIssueCode.EMPTY_PAGE.value in codes and (
        observation.sections or observation.interactive_elements
    ):
        codes.discard(PageIssueCode.EMPTY_PAGE.value)
    return bool(codes & _HARD_BLOCKER_CODES)


def _element_text(element: InteractiveElement) -> str:
    return " ".join(
        part
        for part in (
            element.role,
            element.accessible_name,
            element.visible_text,
            element.target_url,
            element.input_type,
        )
        if part
    )


def _truncate_text(text: str, limit: int) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(limit - 1, 0)]}..."
