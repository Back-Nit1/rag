/**
 * The in-app notification feed.
 *
 * This is the delivery record the product can actually vouch for. A push that
 * the platform dropped does not appear as delivered, and a read receipt is a
 * separate fact from a transport accepting the message.
 */
import { Button } from "../components/Button";
import { Card, Empty, SectionHeading } from "../components/Card";
import { useSession } from "../app/session";
import { timeLabel } from "../lib/format";

export function NotificationsScreen() {
  const { notifications, markRead, timezone, notificationPermission, refreshNotifications } = useSession();

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <SectionHeading hint={`${notifications.length} in the feed`}>Notifications</SectionHeading>
        {notificationPermission !== "granted" && (
          <p className="mb-3 rounded-lg border border-warn/40 bg-warn-soft px-3 py-2 text-xs text-warn">
            System notifications are off, so reminders appear here only. Nothing was marked as delivered to your
            device.
          </p>
        )}
        <Button variant="quiet" className="mb-2 px-0" onClick={() => void refreshNotifications()}>
          Refresh
        </Button>
        {notifications.length === 0 ? (
          <Empty>No reminders have fired yet.</Empty>
        ) : (
          <ul className="flex flex-col gap-2">
            {notifications.map((notification) => (
              <li
                key={notification.id}
                className={`rounded-lg border px-3 py-2 ${notification.read_at ? "border-line bg-canvas" : "border-accent/40 bg-accent-soft"}`}
              >
                <p className="text-sm font-medium text-ink">{notification.title}</p>
                <p className="text-xs text-muted">{notification.body}</p>
                <p className="mt-1 text-[11px] text-muted">
                  {timeLabel(notification.created_at, timezone)} · {notification.channel} ·{" "}
                  {notification.delivery_status ?? "unknown"} delivery
                  {notification.read_at ? " · read" : ""}
                </p>
                {!notification.read_at && (
                  <Button variant="quiet" className="mt-1 px-0" onClick={() => void markRead(notification.id)}>
                    Mark read
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
