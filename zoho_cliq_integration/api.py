# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

"""What Cliq calls when somebody presses a button on a card.

One entry point, and it answers in Cliq's language: every reply is a
`message_edit` map, which the Deluge handler returns untouched and Cliq renders
over the message the button was pressed on. That is what makes the card correct
itself -- the approval, the refusal and the "you are too late" all arrive as the
same shape, and the handler needs no logic of its own.

The design rule here is that **the card is not the authority**. A card is a
picture of the document taken when the message was sent, and it can be hours
stale: approved in ERPNext since, approved from another device, forwarded to
somebody who was never the approver. So nothing in the press is trusted. The
document is re-read, the presser is checked against the person the message was
addressed to, and the state is checked against the one the button claims to act
on. Only then is the workflow touched.
"""

import frappe
import frappe.utils
from frappe.model.workflow import apply_workflow

from zoho_cliq_integration.notifications import material_request as mr

DOCTYPE = mr.DOCTYPE


@frappe.whitelist()
def material_request_action(mr_name: str, action: str, actor: str) -> dict:
	"""Apply one approver's decision, and answer with the card as it now is.

	Authenticated by Frappe in the ordinary way -- the Cliq function carries an
	API key and secret for a dedicated user, so this is not `allow_guest` and
	there is no hand-rolled shared secret to leak or rotate. `actor` is the
	Cliq account that pressed the button, which is a claim about *who*, not a
	credential: it is checked against the person the message was sent to, and
	it is the reason a key alone cannot approve anything.

	Every path returns a card. A refusal that returned only an error message
	would leave the stale card sitting there, still offering a button that no
	longer does anything -- which is the exact failure this is supposed to end.
	"""
	action = (action or "").strip().lower()
	workflow_action = mr.WORKFLOW_ACTIONS.get(action)
	if not workflow_action:
		frappe.throw(f"'{action}' is not an action this card offers.")

	doc = frappe.get_doc(DOCTYPE, mr_name)

	refusal = _refusal(doc, actor)
	if refusal:
		return _edit(doc, note=refusal)

	# Applied as the approver, not as the key-holder. The workflow's own rules
	# then apply to the person who actually pressed the button -- the
	# 'Projects Manager' role on every transition out of this state, and the
	# self-approval bar -- and the document records them in `modified_by`
	# rather than an integration account.
	original_user = frappe.session.user
	frappe.set_user(_user_id(actor))
	try:
		apply_workflow(doc, workflow_action)
	except Exception as exc:
		# The workflow said no: the presser lacks the role, or raised the
		# request themselves, or a validation on the document failed. None of
		# those are this app's business to judge, and all of them have to come
		# back as a card -- a traceback returned to Cliq renders as a broken
		# message and leaves the approver with no idea what happened.
		frappe.db.rollback()
		doc.reload()
		return _edit(doc, note=_plain(exc))
	finally:
		frappe.set_user(original_user)

	doc.reload()
	return _edit(doc)


def _plain(exc) -> str:
	"""A Frappe exception as one line an approver can read.

	Frappe raises validation messages as HTML -- bold tags, links, sometimes a
	whole list -- and a table cell renders none of it. The message still says
	the useful part, so it is stripped rather than replaced.
	"""
	message = frappe.utils.strip_html(str(exc) or "").strip()
	message = " ".join(message.split()) or "ERPNext would not apply this action."
	return message[:200]


def _refusal(doc, actor: str) -> str | None:
	"""Why this press must not move the document, or None if it may.

	Both checks answer requirement 5, and they answer different halves of it.
	The state check catches the document that moved after the card was sent --
	approved in ERPNext, approved from another device, approved twice because
	the approver pressed twice. The identity check catches the press that came
	from somebody who was never being asked: a card can be forwarded, and the
	button travels with it.
	"""
	if doc.get(mr.common.STATE_FIELD) != mr.NOTIFY_STATE:
		return (
			f"Already {doc.get(mr.common.STATE_FIELD)}. This card was sent when "
			f"it was still waiting, so the buttons are gone now."
		)

	actor_email = mr.common.resolve_email(actor) or (actor or "").strip().lower()
	intended, _source, _reason = mr.resolve_project_manager(doc, mr.NOTIFY_STATE)
	intended_email = mr.common.resolve_email(intended) if intended else None

	if not actor_email or actor_email != (intended_email or "").lower():
		return "This request is not waiting on you, so nothing was changed."

	user = _user_id(actor_email)
	if not user:
		return "No ERPNext user matches your Cliq account, so nothing was changed."

	return _missing_role(doc, user)


def _missing_role(doc, user: str) -> str | None:
	"""The role this transition wants and the presser has not, or None.

	Checked before the attempt rather than left to `apply_workflow`, which
	refuses a transition the user may not make with 'Not a valid Workflow
	Action' -- true, and no use at all to somebody holding a phone. Being the
	approver named on the document and holding the role that may approve it
	are two different things, and they come apart often enough that the
	difference has to be said out loud.
	"""
	workflow = frappe.db.get_value(
		"Workflow", {"document_type": doc.doctype, "is_active": 1}, "name"
	)
	if not workflow:
		return None

	allowed = frappe.get_all(
		"Workflow Transition",
		filters={"parent": workflow, "state": doc.get(mr.common.STATE_FIELD)},
		pluck="allowed",
	)
	if not allowed:
		return None

	if set(frappe.get_roles(user)).intersection(allowed):
		return None

	return (
		f"You are the approver named on this request, but acting on it needs "
		f"the '{allowed[0]}' role, which your ERPNext user does not have. "
		f"Nothing was changed."
	)


def _user_id(actor: str) -> str | None:
	"""The ERPNext login behind a Cliq address, or None.

	A Cliq account is an address; a workflow is applied by a User. They are
	usually the same string, but `resolve_email` exists because they are not
	always, and the lookup has to end at a real User or the action cannot be
	attributed to anybody.
	"""
	email = mr.common.resolve_email(actor) or (actor or "").strip().lower()
	if not email:
		return None
	return frappe.db.get_value("User", {"name": email, "enabled": 1}, "name") or frappe.db.get_value(
		"User", {"email": email, "enabled": 1}, "name"
	)


def _edit(doc, note: str | None = None) -> dict:
	"""The document, as the message that should now be on screen.

	`type: message_edit` is what tells Cliq to replace the message the button
	was pressed on rather than post a reply -- the requirement was one card per
	request that stays current, not a conversation about it.

	The card is built by the same `build_card` the notification uses, from the
	document's state as it now is, so the status, the buttons and the rows
	cannot drift apart from what a freshly sent message would say.
	"""
	state = doc.get(mr.common.STATE_FIELD)
	context = mr.base_context(doc, state)
	if note:
		context["note"] = note

	card = mr.build_card(doc, context)
	return {
		"type": "message_edit",
		"text": f"*{mr.SUBJECTS[mr.APPROVER].format(**context)}*",
		"card": card["card"],
		"slides": card["slides"],
		"buttons": card["buttons"],
	}
