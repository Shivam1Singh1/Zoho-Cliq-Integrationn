# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

"""Cliq Message Log -- one row per message, sent or not.

Two jobs at once. It is the **audit trail**: who was meant to get this, who
it actually went to, which rule named them, and what Zoho said. It is also
the **duplicate guard**: `cliq.client` matches on (document, transition,
target) before queueing, so the rows are load-bearing, not just a record.

Rows are written by `cliq.client`, never by a user; every field is read-only.
"""

import frappe
from frappe.model.document import Document


class CliqMessageLog(Document):
	"""No controller logic on purpose.

	Every field is read-only and every row is written by `cliq.client`, so
	there is no user input to validate. Adding a `validate` here would run on
	the status updates delivery makes, which is not what it would be for.
	"""

	pass


def clear_old_logs(days: int = 90):
	"""Drop old rows that nobody needs any more.

	Only outcomes that succeeded or were deliberately not sent -- `Sent`,
	`Dry Run`, `Skipped`. `Failed` and `Undelivered` are kept at any age,
	because those are the ones somebody still has to look at, and a message
	that never arrived does not stop mattering after ninety days.

	Not scheduled. Call it from a scheduler event or `bench execute` if the
	table grows; on a site with `log_successful_sends` off it stays small by
	itself.
	"""
	frappe.db.delete(
		"Cliq Message Log",
		{
			"status": ("in", ("Sent", "Dry Run", "Skipped")),
			"creation": ("<", frappe.utils.add_days(frappe.utils.nowdate(), -days)),
		},
	)
