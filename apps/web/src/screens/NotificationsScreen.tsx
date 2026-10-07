/**
 * The in-app notification feed.
 *
 * This is the delivery record the product can actually vouch for. A push that
 * the platform dropped does not appear as delivered, and a read receipt is a
 * separate fact from a transport accepting the message.
 */
import { Button } from "../components/Button";
import { ReminderControls } from "../components/ReminderControls";
import { Empty, Notice, PageHeader } from "../components/Card";
import { useSession } from "../app/session";
import { timeLabel } from "../lib/format";

export function NotificationsScreen() {
  const { notifications, markRead, timezone, notificationPermission, refreshNotifications } = useSession();

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Alerts"
        sub={`${notifications.length} in the feed`}
        action={
          <Button onClick={() => void refreshNotifications()}>Refresh</Button>
        }
      />
      {notificationPermission !== "granted" && (
        <Notice tone="warn">
          {notificationPermission === "unsupported"
            ? "This browser cannot show system notifications. Reminders appear here only, and nothing is marked as delivered to your device."
            : "System notifications are off, so reminders appear here only. Nothing was marked as delivered to your device."}
        </Notice>
      )}
      {notifications.length === 0 ? (
        <Empty icon="bell" title="No reminders have fired yet">
          When a reminder comes due it is recorded here first, whether or not your device could show it.
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {notifications.map((notification) => (
            <li
              key={notification.id}
              className={`defer-paint rounded-lg border p-4 ${
                notification.read_at
                  ? "border-line bg-surface shadow-card"
                  : "border-accent/60 border-l-4 bg-accent-soft"
              }`}
            >
              <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
                <div className="min-w-0 flex-1 basis-56">
                  <p className="font-serif text-base font-semibold text-ink">
                    {!notification.read_at && <span className="mr-2 text-xs font-sans font-medium text-accent-ink">Unread</span>}
                    {notification.title}
                  </p>
                  <p className="text-sm text-muted">{notification.body}</p>
                  <p className="mt-1.5 text-xs text-muted">
                    {timeLabel(notification.created_at, timezone)} <span aria-hidden="true">·</span>{" "}
                    {notification.channel} <span aria-hidden="true">·</span>{" "}
                    {notification.delivery_status ?? "unknown"} delivery
                    {notification.read_at ? " · read" : ""}
                  </p>
                  {notification.reminder_id && (
                    <ReminderControls reminderId={notification.reminder_id} timezone={timezone} />
                  )}
                </div>
                {!notification.read_at && (
                  <Button onClick={() => void markRead(notification.id)}>Mark read</Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
