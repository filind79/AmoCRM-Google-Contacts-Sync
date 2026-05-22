from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from loguru import logger

from app.config import settings
from app.integrations.telegram_client import send_telegram_alert


class AlertCategory(str, Enum):
    GOOGLE_AUTH_REAUTH_REQUIRED = "google_auth_reauth_required"
    PROCESSING_LOOP_STALLED_UNRECOVERED = "processing_loop_stalled_unrecovered"
    QUEUE_BACKLOG_UNRECOVERED = "queue_backlog_unrecovered"
    SERVICE_MANUAL_RESTART_RECOMMENDED = "service_manual_restart_recommended"


@dataclass(frozen=True)
class AlertTemplate:
    title: str
    requires_manual_action: bool
    action_text: str
    action_url: Optional[str]
    recovery_message_enabled: bool = True


def _dashboard_url() -> Optional[str]:
    return settings.render_service_dashboard_url or None


def _templates() -> dict[AlertCategory, AlertTemplate]:
    dashboard_url = _dashboard_url()
    return {
        AlertCategory.GOOGLE_AUTH_REAUTH_REQUIRED: AlertTemplate(
            title="Потеряна авторизация Google Contacts",
            requires_manual_action=True,
            action_text="Перейдите по ссылке и заново авторизуйте Google Contacts",
            action_url="https://amocrm-google-contacts-sync.onrender.com/auth/google/start",
        ),
        AlertCategory.PROCESSING_LOOP_STALLED_UNRECOVERED: AlertTemplate(
            title="Очередь синхронизации не обрабатывается",
            requires_manual_action=True,
            action_text="Проверьте Render logs и при необходимости перезапустите сервис",
            action_url=dashboard_url,
        ),
        AlertCategory.QUEUE_BACKLOG_UNRECOVERED: AlertTemplate(
            title="Накопилась необрабатываемая очередь синхронизации",
            requires_manual_action=True,
            action_text="Проверьте состояние очереди и перезапустите сервис при необходимости",
            action_url=dashboard_url,
        ),
        AlertCategory.SERVICE_MANUAL_RESTART_RECOMMENDED: AlertTemplate(
            title="Рекомендуется ручной перезапуск сервиса",
            requires_manual_action=True,
            action_text="Откройте Render dashboard, проверьте логи и выполните restart",
            action_url=dashboard_url,
        ),
    }


def _build_message(*, service_name: str, problem: str, status: str, action_text: str, action_url: Optional[str], technical: Optional[str] = None) -> str:
    parts = [
        f"Сервис: {service_name}",
        f"Проблема: {problem}",
        f"Статус: {status}",
        f"Что делать: {action_text}",
    ]
    if action_url:
        parts.append(f"Ссылка: {action_url}")
    if technical:
        parts.append(f"Технически: {technical}")
    return "\n".join(parts)


def send_problem_alert(category: AlertCategory, *, technical: Optional[str] = None) -> bool:
    tpl = _templates()[category]
    status = "Нужно ручное действие" if tpl.requires_manual_action else "Нужно внимание"
    if not tpl.action_url and "Render" in tpl.action_text:
        action_text = "Проверьте сервис в Render dashboard"
    else:
        action_text = tpl.action_text
    message = _build_message(
        service_name=settings.service_display_name,
        problem=tpl.title,
        status=status,
        action_text=action_text,
        action_url=tpl.action_url,
        technical=technical,
    )
    sent = send_telegram_alert(message)
    if not sent:
        return False
    logger.info("telegram_alert.sent_manual_action_required category=%s", category.value)
    return True


def send_recovery_alert(category: AlertCategory, *, technical: Optional[str] = None) -> bool:
    tpl = _templates()[category]
    if not tpl.recovery_message_enabled:
        return False
    message = _build_message(
        service_name=settings.service_display_name,
        problem=tpl.title,
        status="Восстановлено",
        action_text="Действий не требуется",
        action_url=None,
        technical=technical,
    )
    sent = send_telegram_alert(message)
    if not sent:
        return False
    logger.info("telegram_alert.recovery_sent category=%s", category.value)
    return True
