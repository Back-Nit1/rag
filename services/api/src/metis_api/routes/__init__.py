"""Route modules. Each one parses, authorises and serialises — nothing more."""

from . import captures, notifications, plans, projects, reminders, tasks

__all__ = ["captures", "notifications", "plans", "projects", "reminders", "tasks"]
