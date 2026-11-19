# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

"""One row of Cliq Notification Settings: a doctype that may send.

Validation runs from the parent, not from this child's own `validate`, so
that duplicate detection can see every row at once.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class CliqNotificationDoctype(Document):
	"""A single Document Type row in the parent's table."""

	def validate_row(self, seen: set):
		"""Check this row. `seen` accumulates across rows, so pass it along."""
		self.validate_not_duplicate(seen)
		self.warn_if_no_active_workflow()

	def validate_not_duplicate(self, seen: set):
		"""Refuse the same doctype twice.

		A duplicate is not harmful -- the handler reads a de-duplicated set --
		but it reads as two rules where there is one, and someone removing the
		"first" row to stop messages would find they keep arriving.
		"""
		document_type = (self.document_type or "").strip()
		if not document_type:
			return

		if document_type in seen:
			frappe.throw(
				_("Row {0}: {1} is already in the list.").format(
					self.idx, frappe.bold(document_type)
				),
				title=_("Duplicate Doctype"),
			)

		seen.add(document_type)

	def warn_if_no_active_workflow(self):
		"""Warn, do not block, when the doctype has no active Workflow.

		This app reacts to workflow-state changes, so a doctype without one can
		never send. It is a warning rather than an error because the Workflow
		may be created minutes later, and refusing the save would be wrong.
		"""
		if not self.document_type:
			return

		if frappe.db.exists(
			"Workflow", {"document_type": self.document_type, "is_active": 1}
		):
			return

		frappe.msgprint(
			_("{0} has no active Workflow, so no Cliq message can be sent for it.").format(
				frappe.bold(self.document_type)
			),
			title=_("No Active Workflow"),
			indicator="orange",
		)
