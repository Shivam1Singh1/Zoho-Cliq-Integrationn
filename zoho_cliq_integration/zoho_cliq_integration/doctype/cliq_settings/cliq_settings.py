# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

"""Cliq Settings -- the single place this integration is configured.

Validation here exists to stop two specific mistakes: a half-configured
integration being switched live, and an allowlist that silently matches
nothing because of stray whitespace or a leading @.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class CliqSettings(Document):
	"""The Single. See `validate` for what it refuses to let you save."""

	def validate(self):
		"""Normalise what is forgiving, and refuse what is dangerous.

		The one hard refusal is leaving Dry Run with bot, domain or token
		missing -- that combination looks live and sends nothing, and the
		failure surfaces only when someone notices messages never arrived.
		"""
		self.normalise_allowed_domains()

		self.api_domain = (self.api_domain or "").strip().rstrip("/")

		for scheme in ("https://", "http://"):
			if self.api_domain.startswith(scheme):
				self.api_domain = self.api_domain[len(scheme) :]

		self.bot_unique_name = (self.bot_unique_name or "").strip()

		if self.timeout and self.timeout < 1:
			frappe.throw(_("Request Timeout must be at least 1 second."))

		if self.max_attempts and self.max_attempts < 1:
			frappe.throw(_("Max Attempts must be at least 1."))

		if self.enabled and not self.dry_run:
			missing = [
				label
				for label, value in (
					(_("Bot Unique Name"), self.bot_unique_name),
					(_("API Domain"), self.api_domain),
					(_("Zapikey"), self.get_password("zapikey", raise_exception=False)),
				)
				if not value
			]
			if missing:
				frappe.throw(
					_("Cannot leave Dry Run with these fields empty: {0}").format(
						", ".join(missing)
					)
				)


	def normalise_allowed_domains(self):
		"""Accept whatever the administrator typed; store one clean form.

		Commas or newlines, any case, with or without a leading @. The guard
		compares with `endswith("@" + domain)`, so an unnormalised entry like
		" @MicroCRISPR.com " would match nobody and silently block everyone.
		"""
		raw = (self.allowed_email_domains or "").replace("\n", ",")
		domains = []
		for part in raw.split(","):
			part = part.strip().lower().lstrip("@")
			if part and part not in domains:
				domains.append(part)
		self.allowed_email_domains = ", ".join(domains)


def get_settings():
	"""Kept for callers importing from the doctype module.

	`cliq.client.get_settings` is the one the app actually uses.
	"""
	return frappe.get_cached_doc("Cliq Settings")
