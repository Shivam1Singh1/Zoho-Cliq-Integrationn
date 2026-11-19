# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

"""Install and upgrade wiring.

Three jobs, all idempotent, all safe to run on every migrate:

    seed_settings()            create the Single, off and in dry run
    seed_notification_doctype()  switch Material Request on, once
    remove_cliq_channel()      undo the Notification channel this app
                               used to add to a core doctype

Each seeds only what is unset. An administrator who has chosen a value --
including clearing one on purpose -- is never overruled by a later migrate.
"""

import frappe

CLIQ = "Cliq"


NOTIFY_DOCTYPE = "Material Request"


def after_install():
	"""First install: create the settings, and switch the one doctype on."""
	seed_settings()
	seed_notification_doctype()


def after_migrate():
	"""Every migrate. Each step is a no-op once it has already run."""
	remove_cliq_channel()
	seed_allowed_domains()
	seed_notification_doctype()


def seed_notification_doctype():
	"""Put Material Request in the list, once, on a site that has no list.

	The handler will not fire for a doctype that is not listed, so an empty
	table means nothing is sent -- a silent install is worse than a wrong one.
	An administrator who clears the row has said no on purpose, and a table
	with anything in it is left alone.
	"""
	settings = frappe.get_single("Cliq Notification Settings")
	if settings.doctypes_list:
		return

	settings.append("doctypes_list", {"document_type": NOTIFY_DOCTYPE})
	settings.save(ignore_permissions=True)


def seed_allowed_domains():
	"""Close the recipient guard on first upgrade, not on every migrate.

	Only an unset field is filled. Once an administrator has chosen a value --
	including clearing it to allow every domain -- it is left alone.
	"""
	settings = frappe.get_single("Cliq Settings")
	if settings.get("allowed_email_domains") is not None:
		return

	settings.allowed_email_domains = "microcrispr.com"
	settings.save(ignore_permissions=True)


def remove_cliq_channel():
	"""Withdraw the `Cliq` channel this app used to add to Notification.

	Order matters: the rules are disabled *before* the option is removed.
	Once `Cliq` is gone from the options a Notification still set to it fails
	validation on its next save, so leaving one enabled would strand it.

	They are disabled, never deleted -- the rule, its condition and its
	template all remain readable, and nothing a user authored is destroyed.

	Lives in `after_migrate` rather than a one-shot patch so a site restored
	from an older backup is cleaned up too. After the first run it is two
	cheap queries that find nothing.
	"""
	rules = frappe.get_all("Notification", filters={"channel": CLIQ}, pluck="name")

	for name in rules:
		frappe.db.set_value("Notification", name, "enabled", 0, update_modified=False)

	for row in frappe.get_all(
		"Property Setter",
		filters={
			"doc_type": "Notification",
			"field_name": "channel",
			"property": "options",
		},
		fields=["name", "value"],
	):
		if CLIQ in (row.value or ""):
			frappe.delete_doc(
				"Property Setter", row.name, ignore_permissions=True, force=True
			)

	frappe.clear_cache(doctype="Notification")

	if rules:
		frappe.log_error(
			title="Cliq: Notification channel withdrawn",
			message=(
				"This app no longer delivers over Frappe's Notification doctype. "
				"'Cliq' has been removed from the channel options, and these rules "
				"were disabled because their channel no longer exists:\n\n"
				+ "\n".join(f"  - {name}" for name in rules)
				+ "\n\nThey are disabled, not deleted, so nothing is lost. To get "
				"these alerts back, tick the doctype in Cliq Notification Settings "
				"instead: recipients then come from the doctype's own Workflow "
				"rather than from a rule's recipient list.\n\n"
				"Leaving them enabled was not an option -- the channel option is "
				"gone, so saving such a rule now fails validation."
			),
		)


def seed_settings():
	"""Create the Single with safe defaults: disabled, and in dry run.

	Keyed on `api_domain` being empty, so it runs exactly once. Defaulting to
	off-and-dry-run means installing this app cannot message anybody until
	someone deliberately turns it on.
	"""
	settings = frappe.get_single("Cliq Settings")
	if settings.get("api_domain"):
		return

	settings.enabled = 0
	settings.dry_run = 1
	settings.api_domain = "cliq.zoho.in"
	settings.identity_field = "user_id"
	settings.timeout = 10
	settings.max_attempts = 3
	settings.log_successful_sends = 1
	settings.allowed_email_domains = "microcrispr.com"
	settings.save(ignore_permissions=True)
