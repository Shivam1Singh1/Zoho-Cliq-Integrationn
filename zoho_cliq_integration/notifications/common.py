# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

"""What every doctype handler needs and none of them should own.

Nothing in this module names a doctype, reads a doctype-specific field, or
contains a sentence a user will read. It is the machinery underneath:

    state_transition()  did the workflow state actually move?
    enqueue_job()       hand the transition to a worker, exactly once
    deliver()           turn a plan into log rows and queued messages
    resolve_email()     a User link, an Employee id or an address -> an address
    dedupe()            one message per person, never one to the actor
    render()            template + context -> message body
    form_url()          a document -> a link that opens from outside
    state_label()       (old, new) -> the duplicate guard's key

Only one handler uses these today. The module stays because it is the seam a
second doctype would be added along, and because none of it names a doctype --
every function here would serve the next one unchanged.
"""

import frappe
from frappe.utils import get_url_to_form

from zoho_cliq_integration.cliq.client import log_skipped, queue_message

STATE_FIELD = "workflow_state"

# Shared logins, not people with Cliq accounts.
NOT_A_PERSON = ("Administrator", "Guest")


# ---------------------------------------------------------------------------
# the hook guard
# ---------------------------------------------------------------------------


def state_transition(doc, state_field: str = STATE_FIELD):
	"""`(old_state, new_state)` for a real move, or `None`.

	The cheap checks come first and in this order on purpose.
	`has_value_changed` is an in-memory field comparison, so a save that did
	not touch the workflow state -- the overwhelming majority -- costs that
	and nothing else. Only then is the before-snapshot read.

	A document created directly into a state has no `before`; that is a real
	transition with an empty `old_state`, not a non-event. A save that rewrites
	the state to the value it already held is not.
	"""
	if not doc.has_value_changed(state_field):
		return None

	new_state = (doc.get(state_field) or "").strip()
	if not new_state:
		return None

	before = doc.get_doc_before_save()
	old_state = (before.get(state_field) or "").strip() if before else ""
	if old_state == new_state:
		return None

	return old_state, new_state


def enqueue_job(method: str, job_prefix: str, name: str, old_state: str, new_state: str, **kwargs):
	"""Queue the job for after commit, once per transition.

	One transition saves the document more than once -- seven to eight hook
	invocations were observed for a single approval -- so the same message
	would otherwise go out twice. The `job_id` alone does *not* dedupe:
	`frappe.enqueue` takes `deduplicate=False` by default, which is why it is
	passed explicitly.
	"""
	frappe.enqueue(
		method,
		queue="short",
		enqueue_after_commit=True,
		deduplicate=True,
		job_id=f"{job_prefix}::{name}::{old_state}->{new_state}",
		old_state=old_state,
		new_state=new_state,
		actor=frappe.session.user,
		**kwargs,
	)


# ---------------------------------------------------------------------------
# the plan -> the queue
# ---------------------------------------------------------------------------


def deliver(doctype: str, name: str, plan: dict, label: str) -> list[str]:
	"""Write the skips to the log and hand each message to the sender.

	`label` is the duplicate-guard key, so a handler that sends more than one
	message per transition would need to vary it per recipient -- in Channel
	mode they all resolve to the same channel and the guard would swallow all
	but the first. The one handler here sends exactly one message, so the plain
	label is both correct and the more readable thing to see in the log.
	"""
	for skip in plan.get("skips") or []:
		log_skipped(
			reason=skip,
			reference_doctype=doctype,
			reference_name=name,
			trigger_state=label,
		)

	queued = []
	for message in plan.get("messages") or []:
		queued += queue_message(
			recipient_user=message["email"],
			subject=message["subject"],
			message=message["body"],
			reference_doctype=doctype,
			reference_name=name,
			trigger_state=label,
			# Optional: a handler with nothing structured to say omits it and
			# the message goes out as plain text, exactly as before.
			card=message.get("card"),
			# Which rule named this person. A wrong recipient is otherwise an
			# address with no way back to the field that produced it.
			resolved_via=message.get("resolved_via"),
		)
	return queued


def dedupe(messages: list, actor: str) -> list:
	"""One message per person, and never one back to whoever just acted.

	The comparison is on the address, not the user id: the same person can
	arrive as a User link from one source and an Employee id from another, and
	only the address is a reliable identity.
	"""
	actor_email = resolve_email(actor)

	kept, seen = [], set()
	for message in messages:
		email = (message.get("email") or "").strip().lower()
		if not email or email == actor_email or email in seen:
			continue
		seen.add(email)
		kept.append(message)
	return kept


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------


def resolve_email(user_link) -> str | None:
	"""Best email for a User link, an Employee id, or an address itself.

	The approver fields these handlers read are `Link -> User` *or*
	`Link -> Employee`, and several hold employee numbers with no User record
	behind them, so a User lookup alone is not enough. Falls through the
	Employee fields in the order this site actually populates them:
	`user_id` (696/710), `company_email` (686), `prefered_email` (683).
	"""
	user_link = (user_link or "").strip()
	if not user_link or user_link in NOT_A_PERSON:
		return None

	email = frappe.db.get_value("User", user_link, "email")
	if email and "@" in email:
		return email.strip().lower()

	for filters in ({"user_id": user_link}, {"name": user_link}, {"employee_number": user_link}):
		employee = frappe.db.get_value(
			"Employee", filters, ["user_id", "company_email", "prefered_email"], as_dict=True
		)
		if not employee:
			continue
		for field in ("user_id", "company_email", "prefered_email"):
			value = (employee.get(field) or "").strip()
			if "@" in value:
				return value.lower()

	return user_link.lower() if "@" in user_link else None


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def render(template: str, context: dict) -> str:
	"""Fill a wording template from its context.

	A missing placeholder raises KeyError rather than rendering a half-written
	sentence, which is what keeps the template block and `base_context` in
	step: a typo fails the job and lands in the Error Log, it does not reach
	somebody's phone.
	"""
	return template.format(**context)


def form_url(doctype: str, name: str) -> str:
	"""A link to the document that opens from outside ERPNext.

	Built from `host_name` in site_config. With that unset, Frappe falls back
	to the site's own name and produces something like `http://site_local/...`
	-- a valid-looking link that opens nothing on the reader's machine.

	Falls back to the bare document name if URL building fails, because a
	message with a plain id still tells the approver what to look at.
	"""
	try:
		return get_url_to_form(doctype, name)
	except Exception:
		return name


def state_label(old_state, new_state) -> str:
	"""The transition, as one string -- and as the duplicate guard's key.

	An empty old state means a creation, not a transition out of nowhere.
	"(new) -> Pending" says that; " -> Pending" reads like a lost value. It is
	also part of the guard key, so the spelling has to stay stable: change it
	and every in-flight document becomes eligible to be messaged again.
	"""
	old_state = (old_state or "").strip()
	return f"{old_state} -> {new_state}" if old_state else f"(new) -> {new_state}"
