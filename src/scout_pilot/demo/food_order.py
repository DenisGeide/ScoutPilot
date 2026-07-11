"""Synthetic food ordering demonstration with checkout safety pause."""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from scout_pilot.browser import BrowserEngineConfig, PlaywrightBrowserEngine
from scout_pilot.config import AppConfig
from scout_pilot.context import DeterministicContextBudgeter
from scout_pilot.demo.interview import LocalDemoServer
from scout_pilot.models import PageIssueCode, PageObservation, ToolRequest
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

DEFAULT_FOOD_ORDER_TASK = "Закажи BBQ burger и картошку фри, остановись перед оплатой."
_TARGET_ITEMS = ("BBQ Burger", "French Fries")
_HARD_BLOCKER_CODES = {
    PageIssueCode.CAPTCHA_BLOCKING_PAGE.value,
    PageIssueCode.LOGIN_WALL.value,
    PageIssueCode.EMPTY_PAGE.value,
    PageIssueCode.NAVIGATION_ERROR.value,
    PageIssueCode.OBSERVATION_ERROR.value,
}


@dataclass(frozen=True)
class FoodOrderDemoSettings:
    """Settings for the deterministic synthetic food ordering demo."""

    site_dir: Path = Path("reports/tmp/food-order-demo-site")
    profile_dir: Path = Path(".browser-profiles/food-order-demo")
    report_path: Path = Path("reports/tmp/food-order-demo-report.json")
    replay_path: Path = Path("reports/tmp/food-order-demo-replay.json")
    headless: bool = False
    slow_mo_ms: int = 80

    def __post_init__(self) -> None:
        if self.slow_mo_ms < 0:
            raise ValueError("slow_mo_ms cannot be negative")


@dataclass(frozen=True)
class FoodOrderItemNote:
    """Short safe note about one selected synthetic menu item."""

    item_name: str
    reason: str
    similar_items_considered: tuple[str, ...]

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "item_name": self.item_name,
            "reason": self.reason,
            "similar_items_considered": list(self.similar_items_considered),
        }


@dataclass(frozen=True)
class FoodOrderDemoResult:
    """Outcome returned by the synthetic food ordering demo."""

    success: bool
    message_ru: str
    local_site_url: str
    report_path: Path
    replay_path: Path
    selected_items: tuple[str, ...]
    checkout_reached: bool
    security_pause_count: int


@dataclass(frozen=True)
class LocalFoodOrderSite:
    """Generated synthetic food-ordering site metadata."""

    root: Path
    start_page_name: str = "index.html"


async def run_local_food_order_demo(
    config: AppConfig,
    settings: FoodOrderDemoSettings,
    *,
    progress: ProgressCallback | None = None,
) -> FoodOrderDemoResult:
    """Run a local ordering task without real restaurants, delivery services or payments."""

    site = prepare_local_food_order_site(settings.site_dir)

    def emit(message_ru: str) -> None:
        if progress is not None:
            progress(message_ru)

    emit("Готовлю локальный синтетический сайт доставки еды.")
    emit(
        f"Постоянный профиль браузера настроен: {settings.profile_dir}. "
        "Путь исключен из Git."
    )

    with LocalDemoServer(site.root) as server:
        start_url = server.url_for(site.start_page_name)
        report = DemoReportRecorder(
            demo_name="synthetic_food_order",
            task=DEFAULT_FOOD_ORDER_TASK,
            start_url=start_url,
        )
        context_budgeter = DeterministicContextBudgeter()
        selected_items: list[str] = []
        checkout_reached = False

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
            emit("Открываю локальный сайт заказа еды.")
            navigation = await _execute(
                tool_runtime,
                ToolRequest("browser.navigate", {"url": start_url}),
                report=report,
                phase="open_food_site",
            )
            if not navigation.success:
                return _final_result(
                    report,
                    settings,
                    start_url,
                    selected_items,
                    False,
                    checkout_reached,
                    "Не удалось открыть локальный сайт заказа еды. Подробности записаны в отчет.",
                )

            observation = await _observe(
                observation_engine,
                context_budgeter,
                report,
                phase="restaurant_search",
            )
            if _has_blocking_issue(observation):
                return _final_result(
                    report,
                    settings,
                    start_url,
                    selected_items,
                    False,
                    checkout_reached,
                    "Локальный сайт заказа еды выглядит недоступным или пустым. Демо остановлено.",
                )

            report.record_event(
                "decision",
                phase="search_food",
                message=(
                    "Use the visible generic restaurant search field; do not call real "
                    "delivery services and do not use selectors."
                ),
                task=DEFAULT_FOOD_ORDER_TASK,
            )
            fill_result = await _execute(
                tool_runtime,
                ToolRequest(
                    "browser.fill_by_label",
                    {"label": "Search restaurants or dishes", "value": "BBQ burger fries"},
                ),
                report=report,
                phase="fill_food_search",
                auto_confirm=True,
                confirmation_source="synthetic_food_search",
            )
            if not fill_result.success:
                return _final_result(
                    report,
                    settings,
                    start_url,
                    selected_items,
                    False,
                    checkout_reached,
                    "Не удалось заполнить локальный поиск по еде.",
                )

            emit("Ищу ресторан с BBQ burger и картошкой фри.")
            search_result = await _execute(
                tool_runtime,
                ToolRequest(
                    "browser.click_by_intent",
                    {"target": "Search restaurants", "role": "button"},
                ),
                report=report,
                phase="run_food_search",
            )
            if not search_result.success:
                return _final_result(
                    report,
                    settings,
                    start_url,
                    selected_items,
                    False,
                    checkout_reached,
                    "Не удалось запустить локальный поиск ресторанов.",
                )
            await _wait(tool_runtime, report=report, phase="wait_search_results")

            restaurants = await _observe(
                observation_engine,
                context_budgeter,
                report,
                phase="restaurant_results",
            )
            report.record_page_read(
                phase="restaurant_results",
                title=restaurants.title,
                url=restaurants.url,
                summary="Search results with a synthetic restaurant list.",
            )

            emit("Открываю меню подходящего ресторана.")
            menu_result = await _execute(
                tool_runtime,
                ToolRequest(
                    "browser.click_by_intent",
                    {
                        "target": "Open Grill Lab menu",
                        "role": "button",
                    },
                ),
                report=report,
                phase="open_menu",
            )
            if not menu_result.success:
                return _final_result(
                    report,
                    settings,
                    start_url,
                    selected_items,
                    False,
                    checkout_reached,
                    "Не удалось открыть локальное меню ресторана.",
                )
            await _wait(tool_runtime, report=report, phase="wait_menu")

            menu_observation = await _observe(
                observation_engine,
                context_budgeter,
                report,
                phase="menu",
            )
            report.record_page_read(
                phase="menu",
                title=menu_observation.title,
                url=menu_observation.url,
                summary="Visible menu with similar burger and fries options.",
            )

            for item_name, context, similar_items in (
                (
                    "BBQ Burger",
                    "Standalone burger with smoked BBQ sauce and pickles",
                    ("BBQ Bacon Burger", "BBQ Burger Combo", "Classic Burger"),
                ),
                (
                    "French Fries",
                    "Plain crispy potato fries side",
                    ("Loaded Fries", "Cheese Fries"),
                ),
            ):
                emit(f"Добавляю в корзину: {item_name}.")
                report.record_event(
                    "decision",
                    phase=f"choose_{_slug(item_name)}",
                    message="Choose the exact menu item by semantic name and local item context.",
                    selected_item=item_name,
                    similar_items_considered=list(similar_items),
                    context=context,
                )
                selection_observation = await _observe(
                    observation_engine,
                    context_budgeter,
                    report,
                    phase=f"select_{_slug(item_name)}",
                )
                element_id = _find_interactive_element_id(
                    selection_observation,
                    f"Add {item_name}",
                    role="button",
                )
                if element_id is None:
                    return _final_result(
                        report,
                        settings,
                        start_url,
                        selected_items,
                        False,
                        checkout_reached,
                        f"Не удалось однозначно выбрать {item_name}.",
                    )
                report.record_event(
                    "semantic_element_selected",
                    phase=f"select_{_slug(item_name)}",
                    target=f"Add {item_name}",
                    role="button",
                    element_id=element_id,
                    context=context,
                )
                click_result = await _execute(
                    tool_runtime,
                    ToolRequest(
                        "browser.click",
                        {"element_id": element_id},
                    ),
                    report=report,
                    phase=f"add_{_slug(item_name)}",
                )
                if not click_result.success:
                    return _final_result(
                        report,
                        settings,
                        start_url,
                        selected_items,
                        False,
                        checkout_reached,
                        f"Не удалось добавить {item_name} в локальную корзину.",
                    )
                selected_items.append(item_name)
                report.record_note(
                    FoodOrderItemNote(
                        item_name=item_name,
                        reason=context,
                        similar_items_considered=similar_items,
                    ).to_dict()
                )
                await _wait(tool_runtime, report=report, phase=f"wait_{_slug(item_name)}")

            cart_observation = await _observe(
                observation_engine,
                context_budgeter,
                report,
                phase="cart",
            )
            cart_text = _observation_text(cart_observation)
            if not all(item in cart_text for item in _TARGET_ITEMS):
                return _final_result(
                    report,
                    settings,
                    start_url,
                    selected_items,
                    False,
                    checkout_reached,
                    "Корзина не содержит ожидаемые позиции.",
                )
            report.record_page_read(
                phase="cart",
                title=cart_observation.title,
                url=cart_observation.url,
                summary=f"Cart contains: {', '.join(selected_items)}.",
            )

            emit("Перехожу к checkout для проверки заказа.")
            cart_result = await _execute(
                tool_runtime,
                ToolRequest(
                    "browser.click_by_intent",
                    {
                        "target": "Review cart",
                        "role": "button",
                    },
                ),
                report=report,
                phase="open_checkout",
            )
            if not cart_result.success:
                return _final_result(
                    report,
                    settings,
                    start_url,
                    selected_items,
                    False,
                    checkout_reached,
                    "Не удалось перейти к локальному checkout.",
                )
            await _wait(tool_runtime, report=report, phase="wait_checkout")

            checkout_observation = await _observe(
                observation_engine,
                context_budgeter,
                report,
                phase="checkout",
            )
            checkout_text = _observation_text(checkout_observation)
            checkout_reached = "Checkout" in checkout_text and all(
                item in checkout_text for item in _TARGET_ITEMS
            )
            report.record_page_read(
                phase="checkout",
                title=checkout_observation.title,
                url=checkout_observation.url,
                summary="Checkout review is visible; payment has not been confirmed.",
            )
            if not checkout_reached:
                return _final_result(
                    report,
                    settings,
                    start_url,
                    selected_items,
                    False,
                    checkout_reached,
                    "Checkout не показал ожидаемый состав заказа.",
                )

            emit("Остановился перед оплатой и подтверждением заказа.")
            payment_result = await _execute(
                tool_runtime,
                ToolRequest(
                    "browser.click_by_intent",
                    {
                        "target": "Pay and confirm order",
                        "role": "button",
                    },
                ),
                report=report,
                phase="probe_payment_safety",
            )
            if payment_result.status is not ToolExecutionStatus.PAUSED:
                report.record_event(
                    "security_probe_result",
                    status=payment_result.status.value,
                    success=payment_result.success,
                    message=payment_result.message,
                )

            success = (
                tuple(selected_items) == _TARGET_ITEMS
                and checkout_reached
                and any(
                    pause.get("phase") == "probe_payment_safety"
                    and pause.get("risk") == "external_side_effect"
                    for pause in report.security_pauses
                )
            )
            return _final_result(
                report,
                settings,
                start_url,
                selected_items,
                success,
                checkout_reached,
                _final_summary_ru(selected_items, checkout_reached, len(report.security_pauses)),
            )
        finally:
            await browser.stop()


def prepare_local_food_order_site(root: Path) -> LocalFoodOrderSite:
    """Create deterministic synthetic food ordering pages."""

    root.mkdir(parents=True, exist_ok=True)
    _write_food_order_page(root / "index.html")
    return LocalFoodOrderSite(root=root)


def _write_food_order_page(path: Path) -> None:
    path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Synthetic Food Delivery</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; color: #1f2933; }}
    header, main {{ padding: 10px 18px; }}
    header {{ background: #eef5f1; border-bottom: 1px solid #c9d8d0; }}
    h1, h2, h3 {{ margin: 0 0 8px; }}
    section {{ margin: 10px 0; }}
    .row {{ display: grid; gap: 8px 12px; grid-template-columns: repeat(4, minmax(150px, 1fr)); }}
    article {{ border-top: 1px solid #d8e0dc; padding: 6px 0; }}
    article h3 {{ font-size: 14px; margin: 0 0 4px; }}
    article p {{ font-size: 12px; margin: 0 0 4px; }}
    label {{ display: block; font-weight: 700; margin-bottom: 6px; }}
    input {{ min-width: 320px; padding: 8px; border: 1px solid #8aa096; }}
    button {{ margin: 3px 6px 3px 0; padding: 6px 10px; font: inherit; }}
    #cart-list {{ min-height: 24px; }}
  </style>
</head>
<body>
  <header>
    <h1>Food delivery sandbox</h1>
    <p>Local synthetic restaurant flow. It never contacts real delivery services.</p>
  </header>
  <main>
    <section id="search-section" role="search" aria-label="Restaurant search">
      <h2>Search restaurants</h2>
      <label for="food-search">Search restaurants or dishes</label>
      <input id="food-search" type="search" name="query" placeholder="BBQ burger fries">
      <button type="button" onclick="showRestaurants()">Search restaurants</button>
      <p id="search-status">Results are hidden until search runs.</p>
    </section>

    <section id="restaurants" hidden aria-label="Restaurant results">
      <h2>Restaurant results</h2>
      <article>
        <h3>Grill Lab</h3>
        <p>BBQ burgers and fries. Good match for BBQ burger plus a fries side.</p>
        <button type="button" onclick="openMenu()">Open Grill Lab menu</button>
      </article>
      <article>
        <h3>Green Bowl</h3>
        <p>Salads, soups and vegetarian bowls.</p>
        <button type="button">Open Green Bowl menu</button>
      </article>
    </section>

    <section id="menu" hidden aria-label="Grill Lab menu">
      <h2>Grill Lab menu</h2>
      <div class="row">
        { _food_item_html("BBQ Burger", "Standalone burger with smoked BBQ sauce and pickles.", "Add BBQ Burger") }
        { _food_item_html("French Fries", "Plain crispy potato fries side.", "Add French Fries") }
        { _food_item_html("BBQ Bacon Burger", "Similar name, but includes bacon and extra sauce.", "Add BBQ Bacon Burger") }
        { _food_item_html("BBQ Burger Combo", "Similar name, but includes a drink and another side.", "Add BBQ Burger Combo") }
        { _food_item_html("Classic Burger", "Simple burger without BBQ sauce.", "Add Classic Burger") }
        { _food_item_html("Loaded Fries", "Similar fries, but with sauce and toppings.", "Add Loaded Fries") }
        { _food_item_html("Cheese Fries", "Similar fries, but with cheese sauce.", "Add Cheese Fries") }
      </div>
    </section>

    <section id="cart" hidden aria-label="Cart">
      <h2>Cart</h2>
      <p id="cart-status">Cart is empty.</p>
      <ul id="cart-list"></ul>
      <button id="review-cart" type="button" onclick="showCheckout()">Review cart</button>
    </section>

    <section id="checkout" hidden aria-label="Checkout">
      <h2>Checkout</h2>
      <p id="checkout-summary">Review the synthetic order before payment.</p>
      <p>No real restaurant, payment processor or private delivery details are used.</p>
      <button type="button" onclick="markPaid()">Pay and confirm order</button>
    </section>
  </main>
  <script>
    const cart = [];
    function showRestaurants() {{
      document.title = "Synthetic Food Search Results";
      document.getElementById("restaurants").hidden = false;
      document.getElementById("search-status").textContent = "Found Grill Lab for BBQ burger and fries.";
    }}
    function openMenu() {{
      document.title = "Grill Lab Menu";
      document.getElementById("search-section").hidden = true;
      document.getElementById("restaurants").hidden = true;
      document.getElementById("menu").hidden = false;
    }}
    function addItem(name) {{
      if (!cart.includes(name)) {{
        cart.push(name);
      }}
      document.getElementById("cart").hidden = false;
      const list = document.getElementById("cart-list");
      while (list.firstChild) {{
        list.removeChild(list.firstChild);
      }}
      for (const item of cart) {{
        const row = document.createElement("li");
        row.textContent = item;
        list.appendChild(row);
      }}
      document.getElementById("cart-status").textContent = "Cart contains: " + cart.join(", ");
    }}
    function showCheckout() {{
      document.title = "Synthetic Food Checkout";
      document.getElementById("menu").hidden = true;
      document.getElementById("cart").hidden = true;
      document.getElementById("checkout").hidden = false;
      document.getElementById("checkout-summary").textContent = "Checkout items: " + cart.join(", ");
    }}
    function markPaid() {{
      document.title = "Synthetic Food Paid";
    }}
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )


def _food_item_html(name: str, description: str, button_text: str) -> str:
    escaped_name = html.escape(name)
    escaped_description = html.escape(description)
    escaped_button = html.escape(button_text)
    escaped_js_name = html.escape(name, quote=True)
    return (
        f'<article aria-label="{escaped_name} menu item">'
        f"<h3>{escaped_name}</h3>"
        f"<p>{escaped_description}</p>"
        f'<button type="button" onclick="addItem(&quot;{escaped_js_name}&quot;)">'
        f"{escaped_button}"
        f"</button>"
        f"</article>"
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
        user_task=DEFAULT_FOOD_ORDER_TASK,
        observation=observation,
        memory_summaries=(
            "task.user_goal: add BBQ Burger and French Fries to a local synthetic cart.",
            "constraint: no real delivery service, payment processor or private delivery details.",
            "security: confirmation is required before payment or order confirmation.",
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
    auto_confirm: bool = False,
    confirmation_source: str | None = None,
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
    if result.status is not ToolExecutionStatus.PAUSED:
        return result

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
    if not auto_confirm:
        return result

    confirmation_id = str(confirmation.get("confirmation_id") or "")
    confirmed = bool(confirmation_id) and tool_runtime.confirm_pending_action(confirmation_id)
    report.record_event(
        "explicit_confirmation",
        phase=phase,
        confirmation_id=confirmation_id,
        confirmed=confirmed,
        source=confirmation_source or "demo_settings",
    )
    if not confirmed:
        return result

    confirmed_result = await tool_runtime.execute(request)
    report.record_event(
        "tool_result_after_confirmation",
        phase=phase,
        tool_name=request.name,
        status=confirmed_result.status.value,
        success=confirmed_result.success,
        message=confirmed_result.message,
        error_code=confirmed_result.error_code,
        retryable=confirmed_result.retryable,
        data=_tool_data_to_report(confirmed_result.data),
    )
    return confirmed_result


async def _wait(
    tool_runtime: DefaultToolRuntime,
    *,
    report: DemoReportRecorder,
    phase: str,
) -> ToolExecutionResult:
    return await _execute(
        tool_runtime,
        ToolRequest("browser.wait", {"milliseconds": 50}),
        report=report,
        phase=phase,
    )


def _final_result(
    report: DemoReportRecorder,
    settings: FoodOrderDemoSettings,
    local_site_url: str,
    selected_items: list[str],
    success: bool,
    checkout_reached: bool,
    message_ru: str,
) -> FoodOrderDemoResult:
    report.set_final(
        success=success,
        stop_reason=("completed_with_security_pause" if success else "failed"),
        summary_ru=message_ru,
    )
    report_path = report.write(settings.report_path)
    replay_path = report.write_replay(settings.replay_path)
    return FoodOrderDemoResult(
        success=success,
        message_ru=message_ru,
        local_site_url=local_site_url,
        report_path=report_path,
        replay_path=replay_path,
        selected_items=tuple(selected_items),
        checkout_reached=checkout_reached,
        security_pause_count=len(report.security_pauses),
    )


def _final_summary_ru(
    selected_items: list[str],
    checkout_reached: bool,
    pauses: int,
) -> str:
    items = ", ".join(selected_items) if selected_items else "ничего не добавлено"
    checkout = "checkout открыт" if checkout_reached else "checkout не открыт"
    return (
        f"Демо добавило в локальную корзину: {items}; {checkout}. "
        f"Оплата и подтверждение заказа не выполнялись; пауз безопасности: {pauses}."
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
                "text": _truncate_text(section.text, 420),
            }
            for section in observation.sections[:10]
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
            for element in observation.interactive_elements[:24]
        ],
        "form_fields": [
            {
                "id": field.field_id,
                "role": field.role,
                "input_type": field.input_type,
                "label": field.label,
                "placeholder": field.placeholder,
                "value_state": field.value_state,
            }
            for field in observation.form_fields[:8]
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


def _find_interactive_element_id(
    observation: PageObservation,
    accessible_name: str,
    *,
    role: str | None = None,
) -> str | None:
    expected_name = accessible_name.casefold()
    for element in observation.interactive_elements:
        if role is not None and element.role != role:
            continue
        names = (element.accessible_name or "", element.visible_text or "")
        if any(name.casefold() == expected_name for name in names):
            return element.element_id
    return None


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


def _slug(value: str) -> str:
    return "_".join(value.casefold().split())


def _truncate_text(text: str, limit: int) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(limit - 1, 0)]}..."
