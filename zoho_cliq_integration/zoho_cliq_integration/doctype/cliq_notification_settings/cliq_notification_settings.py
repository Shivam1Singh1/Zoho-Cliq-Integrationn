# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

"""Cliq Notification Settings -- the on/off switch, as data.

A doctype listed here is live; remove its row and the handler goes quiet
from the next save onward, with no code change and no restart.

Only Material Request does anything today. Listing anything else is inert:
the wildcard rule that once served arbitrary doctypes has been deleted.
"""

import frappe
from frappe.model.document import Document

CACHE_KEY = "cliq_notification_enabled_doctypes"

PARENT = "Cliq Notification Settings"


class CliqNotificationSettings(Document):
	"""The Single holding the list of active doctypes."""

	def validate(self):
		"""Validate the table as a whole, not row by row.

		`seen` is threaded through every row so duplicate detection can see all
		of them at once -- a child's own `validate` only ever sees itself.
		"""
		seen = set()
		for row in self.doctypes_list or []:
			row.validate_row(seen)

	def on_update(self):
		"""Drop the cache so an added or removed row takes effect immediately.

		Without this the handler keeps reading the old list until the next
		restart -- someone removes the row to stop messages, and messages keep
		going out.
		"""
		clear_cache()


def clear_cache():
	"""Forget the cached list; the next read rebuilds it."""
	frappe.cache().delete_value(CACHE_KEY)


def get_enabled_doctypes() -> list[str]:
	"""The doctypes that may send, from cache.

	This is the first thing the hook calls on a Material Request save, so it
	must cost one cache read and never a query. The inner `build` runs only on
	a cold cache.

	Deliberately **not** whitelisted: it is called from a document hook, never
	from the client, and exposing it would hand any logged-in user the list of
	doctypes wired to an outbound integration.
	"""

	def build():
		"""Rebuild the list on a cold cache.

		De-duplicated and sorted so the cached value is stable: it is compared
		and logged, and a set's iteration order is not.
		"""
		rows = frappe.get_all(
			"Cliq Notification Doctype",
			filters={"parenttype": PARENT, "parentfield": "doctypes_list"},
			pluck="document_type",
		)
		return sorted({(row or "").strip() for row in rows if (row or "").strip()})

	return frappe.cache().get_value(CACHE_KEY, build) or []
