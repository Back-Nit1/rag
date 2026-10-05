"""NotificationTransport adapters.

The in-app feed is the one channel that always exists, which is why a reminder
is never "lost" because push is unsupported or blocked. Web push is additive and
reports its own support state honestly: an unsupported environment is
``UNSUPPORTED``, a lost acknowledgement is an ``accepted`` send that may never
have been seen, and neither is ever presented as "the user was notified".
"""

from __future__ import annotations

import json
import logging
from typing import Any

from metis_contracts import DeliveryStatus, Notification, settings
from metis_contracts.enums import AdapterMode

log = logging.getLogger("metis.scheduler.transport")


class InAppTransport:
    """Writes to the user's own notification feed; always supported."""

    channel = "in_app"

    def __init__(self, state_store: Any, clock: Any) -> None:
        self.mode = getattr(state_store, "mode", AdapterMode.LOCAL)
        self._state = state_store
        self._clock = clock

    def supported(self, user_id: str) -> bool:
        return True

    def send(self, notification: Notification) -> tuple[DeliveryStatus, str | None]:
        # The record IS the delivery for this channel; the caller persists it in
        # the same transaction that claims the occurrence.
        return DeliveryStatus.ACCEPTED, "written to the in-app feed"


class FixtureTransport:
    """Collects sends in memory for tests and local demos."""

    channel = "fixture"
    mode = AdapterMode.FIXTURE

    def __init__(self) -> None:
        self.sent: list[Notification] = []

    def supported(self, user_id: str) -> bool:
        return True

    def send(self, notification: Notification) -> tuple[DeliveryStatus, str | None]:
        self.sent.append(notification)
        return DeliveryStatus.ACCEPTED, "fixture transport (not a real delivery)"


class WebPushTransport:
    """Standard VAPID web push. No paid push SaaS is required.

    The private key is read from Parameter Store at runtime, never from the
    repository or the browser bundle. Push is opt-in and additive: the in-app
    feed remains the record of truth.
    """

    channel = "web_push"
    mode = AdapterMode.CLOUD

    def __init__(self, state_store: Any, clock: Any, *, private_key: str | None = None) -> None:
        self._state = state_store
        self._clock = clock
        self._private_key = private_key
        self._subject = "mailto:metis@localhost"

    def _key(self) -> str | None:
        if self._private_key:
            return self._private_key
        param = settings().aws.vapid_private_key_param
        if not param:
            return None
        try:
            import boto3

            ssm = boto3.client("ssm", region_name=settings().aws.region)
            response = ssm.get_parameter(Name=param, WithDecryption=True)
            self._private_key = response["Parameter"]["Value"]
            return self._private_key
        except Exception as exc:
            log.warning("could not read the VAPID private key: %s", exc)
            return None

    def _subscriptions(self, user_id: str) -> list[Any]:
        lister = getattr(self._state, "list_push_subscriptions", None)
        if lister is None:
            return []
        return [s for s in lister(user_id) if s.disabled_at is None]

    def supported(self, user_id: str) -> bool:
        return bool(self._key()) and bool(self._subscriptions(user_id))

    def send(self, notification: Notification) -> tuple[DeliveryStatus, str | None]:
        key = self._key()
        if not key:
            return DeliveryStatus.UNSUPPORTED, "no VAPID signing key is configured"
        subscriptions = self._subscriptions(notification.user_id)
        if not subscriptions:
            return DeliveryStatus.UNSUPPORTED, "no push subscription is registered for this user"

        try:
            from pywebpush import WebPushException, webpush
        except ImportError:
            return DeliveryStatus.UNSUPPORTED, "the web-push library is not installed"

        payload = json.dumps(
            {
                "title": notification.title,
                "body": notification.body,
                "dedupe_key": notification.dedupe_key,
                "links": notification.entity_links,
            }
        )
        accepted = 0
        failures: list[str] = []
        for subscription in subscriptions:
            try:
                webpush(
                    subscription_info={
                        "endpoint": subscription.endpoint,
                        "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                    },
                    data=payload,
                    vapid_private_key=key,
                    vapid_claims={"sub": self._subject},
                    timeout=10,
                )
                accepted += 1
            except WebPushException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in (404, 410):
                    # The subscription is gone for good; stop sending to it.
                    self._disable(subscription)
                    failures.append(f"subscription expired ({status})")
                else:
                    failures.append(str(exc)[:120])
            except Exception as exc:
                failures.append(str(exc)[:120])

        if accepted:
            # Transport acceptance only. The push service may have accepted a
            # send whose acknowledgement was lost, so this is not proof the
            # user saw anything.
            detail = f"accepted by {accepted} endpoint(s)"
            if failures:
                detail += f"; {len(failures)} failed: {failures[0]}"
            return DeliveryStatus.ACCEPTED, detail
        return DeliveryStatus.FAILED, "; ".join(failures) or "no endpoint accepted the push"

    def _disable(self, subscription: Any) -> None:
        try:
            with self._state.transaction(subscription.user_id) as tx:
                tx.put(
                    "push_subscription",
                    subscription.model_copy(update={"disabled_at": self._clock.now()}),
                    expected_revision=subscription.revision,
                )
        except Exception as exc:  # a disabled-flag write must not fail delivery
            log.warning("could not disable an expired subscription: %s", exc)


def build_notification_transports(state_store: Any, clock: Any) -> dict[str, Any]:
    """Channel -> transport. The in-app feed is always present."""
    transports: dict[str, Any] = {"in_app": InAppTransport(state_store, clock)}
    if settings().aws.vapid_private_key_param or settings().aws.vapid_public_key:
        transports["web_push"] = WebPushTransport(state_store, clock)
    return transports
