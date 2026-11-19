# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

"""Material Request -- the one handler this app has wired up.

A Material Request enters 'Pending from Project Manager'; the project manager
it is now waiting on gets one Cliq message. That is the entire behaviour. No
other state sends anything, and nothing is ever sent back to the requester --
the message is one-directional by design, so an approval, a rejection, a
send-back and a cancellation all pass in silence.

Silence is not the same as leaving it stale. Leaving that state rewrites the
Status row of the card the approver already has, so the message ages with the
document instead of freezing at the moment it was sent. That is an edit and
never a second message; when the edit cannot be made the card stays exactly as
it was sent and the log row records why.

Finding that one project manager is the only hard part, because the approver
is not in the Workflow. `Purchase Requisition Matrix` gates the transition by
role, and 'Projects Manager' has 23 holders -- the role says who *may* act,
never who *must*. The address is on the document instead, in Project Head
Name, and `resolve_project_manager` reads that first; the rest of the chain
exists only for records where the form never filled it in.

Everything about Material Request is in this file: the state it reacts to, how
it finds the recipient, and every word it can say. The machinery underneath --
address resolution, the duplicate guard, the queue -- is in `common.py` and
names no doctype.
"""

import frappe
from frappe.utils import formatdate

from zoho_cliq_integration.cliq import client
from zoho_cliq_integration.cliq.client import get_settings
from zoho_cliq_integration.notifications import common
from zoho_cliq_integration.zoho_cliq_integration.doctype.cliq_notification_settings.cliq_notification_settings import (
	get_enabled_doctypes,
)

DOCTYPE = "Material Request"

# The only state that sends. Every other state in the workflow -- including
# the approvals, rejections and send-backs -- passes in silence.
NOTIFY_STATE = "Pending from Project Manager"

APPROVER = "approver"


# ===========================================================================
# WORDING -- everything a Material Request notification can say
# ===========================================================================
#
# Nothing below this banner is logic, and nothing above it is a sentence.
# Rewording is a change to this block alone. The placeholders are the contract
# between the block and `base_context`:
#
#     mr      MAT-MR-2026-03931
#     url     link to the form
#     reason  remark captured with the action, or ""

# The message is a Cliq card, so the wording is only the one line above it --
# everything else the approver needs is in CARD_ROWS below. There is no footer:
# the card carries the detail and the button carries the link.
SUBJECTS = {
	APPROVER: "{mr}: Please Approve it",
}

MESSAGES = {
	# Deliberately bare of words. The subject is already rendered in bold above
	# it by `build_request`, and repeating the document in a sentence under a
	# card that names it reads as padding.
	#
	# It is not empty, though: `build_request` joins subject and body with a
	# blank line, so a body gives the heading room to breathe above the table.
	# The character is a non-breaking space rather than a plain one because a
	# trailing plain space is trimmed and the gap disappears with it.
	"approver_pending": " ",
}

# Column headings, deliberately blank: every row is self-evidently a field and
# its value, so "Field" and "Value" were a row of the table spent saying
# nothing.
#
# They cannot simply be dropped. A table slide with no `headers` key is
# rejected with HTTP 400 `operation_failed`, and the two strings have to differ
# because each row is a dict keyed by its header -- two identical keys collapse
# into one and the table loses a column. Hence "" and a single space.
CARD_HEADERS = ("", " ")

# What the card shows, in this order. Each entry is (label, fieldname); the
# value is read off the document and formatted by `card_value`. This list is
# the card -- adding a row is one line here and nothing else.
#
# Labels are kept short because the left column is narrow: "Employee Function"
# and "Project Description" each wrapped onto two lines, which doubled the
# height of their row for no added meaning.
# Series and Company are deliberately absent. Every Material Request on this
# site carries the same series and the same company, so both rows said the
# same thing on every card and cost the approver two lines to read nothing.
CARD_ROWS = (
	("Purpose", "material_request_type"),
	# NOT `owner`. The form's 'Created By' is its own field, and the two
	# disagree: MAT-MR-2026-03942 is owned by Administrator but was created by
	# developer@example.com. `owner` is who saved the record, which on a
	# workflow document is often not who raised it.
	("Created By", ("custom_full_name", "custom_created_by", "owner")),
	("Date", "transaction_date"),
	("Stages", "custom_stages"),
	# The project's name, not its code. 'PLTP-2025-0045' identifies the record;
	# 'AAV - Huntington Disease - HTT' is what the approver recognises. The code
	# is the fallback for a record whose name was never fetched.
	("Project", ("custom_project_name", "project")),
	("Description", "project_description"),
	# The people, kept together and kept last. Everything above is the request
	# -- what is being asked for, for which project, by when -- and these three
	# are who stands around it. Split across the table they read as more
	# attributes of the purchase; grouped at the foot they read as a chain of
	# responsibility, which is what they are, and the eye finds the request
	# first because the request is what the decision is about.
	#
	# Both heads are reached through the Employee Function the document names;
	# `follow_link` walks the dots. The two paths are not symmetric because the
	# doctype is not: `function_head_name` is a Data field sitting right there
	# on the function, while `reporting_head` is a Link to User holding a login
	# address -- jitendra.vyas@microcrispr.com -- so the readable name is one
	# hop further on, in the User record.
	("Function Head", "custom_employee_function.function_head_name"),
	# The document's own copy first, the function's second -- the usual
	# nearest-first order, and here it is also newest-first: a Material Request
	# that carries a reporting head was stamped with the one in force when it
	# was raised, while the function's is whoever holds the post today.
	("Reporting Head", ("custom_reporting_head", "custom_employee_function.reporting_head.full_name")),
	# The person this card was sent to, named on the card. It reads as
	# redundant in a direct message -- they know who they are -- and is not,
	# for two reasons: in Channel mode the card is read by people who are not
	# the approver, and a card that names the wrong manager is the one visible
	# sign that routing picked the wrong field. The order mirrors
	# `resolve_project_manager`, so the row shows the field that addressed it.
	("Project Manager", ("custom_project_head_name", "custom_project_lead_name")),
)

# Off, deliberately, and this is the switch to flip when it can be honest.
#
# A sent message cannot be edited without an OAuth token carrying
# ZohoCliq.Messages.UPDATE, so a Status row would say *Pending* on a card for
# a request approved hours ago and keep saying it forever. A row that is
# reliably wrong is worse than no row: the approver stops trusting the card,
# and the card is the whole product here.
#
# Everything behind it is built and tested and stays that way -- the states,
# the colours, the update job, the message ids now kept on every log row. Set
# this True once the token is in Cliq Settings and the row comes back live.
SHOW_STATUS_ROW = False

STATUS_LABEL = "Status"

# What the Status row says, read off the workflow state rather than fixed.
# A card is built on NOTIFY_STATE, so Pending is what it is first written
# with; the other three are the only places that state can go, because
# Approve, Reject and Send Back for Correction are the whole of the workflow
# out of it. ('Rejected by Project Manger' is the workflow's own spelling and
# has to stay wrong here to match it.)
#
# `*text*` is Cliq's bold: single asterisks, not the pair most markdown wants.
# It is the same syntax `build_request` wraps the subject in, and it does
# render inside a table cell -- confirmed against the live bot. Cliq has no
# colour markup for a cell, so the colour is the emoji; it also survives into
# the push preview, where the card is not rendered at all.
STATUS_PENDING = "🟠 *Pending*"
STATUS_BY_STATE = {
	"Approved by Project Manager": "🟢 *Approved*",
	"Rejected by Project Manger": "🔴 *Rejected*",
	"Draft": "🔵 *Sent back for correction*",
}

# The states that rewrite a card already on someone's screen. Reaching one of
# these sends nothing: it edits the message the approval was asked for.
OUTCOME_STATES = tuple(STATUS_BY_STATE)

# Button key -> the workflow action ERPNext is asked to apply. The spelling is
# the workflow's own; `apply_workflow` matches on it exactly.
WORKFLOW_ACTIONS = {"approve": "Approve", "reject": "Reject"}

# Fields holding a login address where the card wants a person. All three are
# `Data`, so none can be walked through like a Link, and each stores
# 'neelendra.kumar@microcrispr.com' where the reader wants 'Neelendra Kumar'.
# The address stands if no User carries a full name -- unlike a document code,
# an address still names the person.
PERSON_FIELDS = (
	"custom_reporting_head",
	"custom_project_head_name",
	"custom_project_lead_name",
)

# Cliq rejects a button label over 20 characters with HTTP 400
# 'input_maxlength_reached', so this is a hard limit, not a style choice.
# "Open MAT-MR-2026-03832" is 22 and was the first thing that failed.
BUTTON_LABEL = "Open in ERPNext"
BUTTON_LABEL_LIMIT = 20

# States where every button is dropped. A button is a call to action, and once
# the document is approved there is nothing left to do -- the link stops being
# an offer and becomes a leftover.
#
# Only the approval. A rejection or a send-back keeps the link, because both
# are states somebody still has to go and look at the document about.
BUTTONLESS_STATES = ("Approved by Project Manager",)

# Acting on the request without leaving Cliq. The label is what the approver
# reads, the key is what the handler is told was pressed, the workflow action
# is what ERPNext is asked to apply, and the type is the outline colour Cliq
# draws: `+` green, `-` red -- the only two it has.
#
# These appear on NOTIFY_STATE alone. A card for a decided document shows no
# way to decide it again, which is the first of the two guards behind
# requirement 5; the second is in `api.material_request_action`, because a
# card already on someone's screen cannot be taken back.
ACTION_BUTTONS = (
	("Approve", "approve", "Approve", "+"),
	("Reject", "reject", "Reject", "-"),
)

# How the document rides along on the button.
#
# It has to ride on `key`, because there is nowhere else. Cliq's send API
# validates a button action against a fixed shape and rejects everything it
# does not recognise -- `arguments`, documented as the way to pass custom
# values, comes back `extra_key_found: 'arguments' is an extra key`, and so
# does any loose key added beside `name` and `owner`. Tested live, all four
# shapes; `key` was the one that posted.
#
# This matters because one approver holds several of these cards at once. The
# handler is told which button was pressed and nothing about the conversation
# around it, so a button that does not name its own document cannot act on it.
ACTION_KEY_SEPARATOR = "::"

# Longest value a table cell may carry before it is cut; 0 would mean never.
#
# Project Description is free text and people paste paragraphs into it --
# MAT-MR-2026-03946 holds 245 characters, which wraps the card into a wall and
# buries the rows above it. Cut at 120 with an ellipsis; the approver opens the
# document for the rest.
CARD_VALUE_LIMIT = 120

# Written into Cliq Message Log when the message cannot be addressed. These
# are routine configuration gaps, not failures, so they never reach the Error
# Log.
SKIPS = {
	"no_project_manager": (
		"{mr} entered '{state}' but no project manager could be found for it. "
		"Neither Project Head Name nor Project Lead Name on the document holds "
		"an email address, project {project} has no Project Lead set, and "
		"Employee Function {ef} maps no Project Head for that project."
	),
	"no_email": (
		"{mr} entered '{state}' and its project manager is {user}, but no "
		"email address could be resolved for them (no User record, and no "
		"Employee with user_id, company_email or prefered_email set)."
	),
}


# ---------------------------------------------------------------------------
# hook entry point
# ---------------------------------------------------------------------------


def on_workflow_state_change(doc, method=None):
	"""Queue the notification when, and only when, the document starts waiting
	on its project manager.

	Registered on both `on_update` and `on_update_after_submit`. Every state in
	this workflow is doc_status 0 except the two terminal approvals, and
	`apply_workflow` reaches those through `doc.submit()`, which runs
	`on_update` before `on_submit` -- so `on_update` alone already sees the
	transition. The second event costs one `has_value_changed` call and covers
	any future allow-on-submit edit.
	"""
	try:
		if doc.doctype != DOCTYPE:
			return

		# The switch an administrator actually turns: remove the Material
		# Request row from Cliq Notification Settings and this app goes quiet
		# without a code change.
		if DOCTYPE not in get_enabled_doctypes():
			return

		transition = common.state_transition(doc)
		if not transition:
			return

		old_state, new_state = transition

		if new_state == NOTIFY_STATE:
			common.enqueue_job(
				f"{__name__}.send_transition_messages",
				"cliq-mr",
				doc.name,
				old_state,
				new_state,
				material_request=doc.name,
			)
			return

		# The approval, the rejection and the send-back still send nothing.
		# They rewrite the Status row of the card the approver was already
		# given, which is why `old_state` has to be the state that card was
		# sent for: a document reaching Draft from anywhere else has no
		# message of ours waiting to be corrected.
		if old_state == NOTIFY_STATE and new_state in OUTCOME_STATES:
			common.enqueue_job(
				f"{__name__}.update_status_messages",
				"cliq-mr-update",
				doc.name,
				old_state,
				new_state,
				material_request=doc.name,
			)
	except Exception:
		# A notification must never cost the user their save.
		frappe.log_error(
			title=f"Cliq MR: could not queue notification for {doc.name}"[:140],
			message=frappe.get_traceback(),
		)


# ---------------------------------------------------------------------------
# the job
# ---------------------------------------------------------------------------


def send_transition_messages(material_request, old_state, new_state, actor):
	"""Resolve the project manager and hand the message to the shared sender."""
	try:
		if not get_settings().enabled:
			return {"ok": False, "skipped": "Cliq Settings is disabled."}

		doc = frappe.get_doc(DOCTYPE, material_request)
		plan = build_plan(doc, new_state, actor)
		label = common.state_label(old_state, new_state)

		return {
			"ok": True,
			"transition": label,
			"queued": common.deliver(DOCTYPE, doc.name, plan, label),
			"skipped": plan["skips"],
		}
	except Exception:
		frappe.log_error(
			title=f"Cliq MR: notification failed for {material_request}"[:140],
			message=frappe.get_traceback(),
		)
		return {"ok": False, "error": "see Error Log"}


def update_status_messages(material_request, old_state, new_state, actor):
	"""Rewrite the Status row on the card already delivered. Send nothing.

	There is no recipient to resolve and no duplicate guard to consult: this
	touches rows that already exist and creates none, so its worst case is a
	message left reading exactly as it read before.

	Rows are matched on any transition *into* NOTIFY_STATE, because the state
	the document came from before approval was asked for is not knowable here
	and does not matter -- what matters is that the row is the one that asked.
	"""
	try:
		if not get_settings().enabled:
			return {"ok": False, "skipped": "Cliq Settings is disabled."}

		doc = frappe.get_doc(DOCTYPE, material_request)
		context = base_context(doc, new_state)

		return {
			"ok": True,
			"transition": common.state_label(old_state, new_state),
			"updated": client.update_sent_card(
				DOCTYPE,
				doc.name,
				build_card(doc, context),
				trigger_state_like=f"%{NOTIFY_STATE}",
				subject=SUBJECTS[APPROVER].format(**context),
				message=common.render(MESSAGES["approver_pending"], context),
			),
		}
	except Exception:
		frappe.log_error(
			title=f"Cliq MR: could not update card for {material_request}"[:140],
			message=frappe.get_traceback(),
		)
		return {"ok": False, "error": "see Error Log"}


# ---------------------------------------------------------------------------
# who hears what
# ---------------------------------------------------------------------------


def build_plan(doc, new_state, actor) -> dict:
	"""Turn the transition into an addressed message, or a skip reason."""
	context = base_context(doc, new_state)

	link, resolved_via, reason = resolve_project_manager(doc, new_state)
	if reason:
		return {"messages": [], "skips": [reason]}

	email = common.resolve_email(link)
	if not email:
		return {
			"messages": [],
			"skips": [SKIPS["no_email"].format(mr=doc.name, state=new_state, user=link)],
		}

	message = {
		"email": email,
		"subject": SUBJECTS[APPROVER].format(**context),
		"body": common.render(MESSAGES["approver_pending"], context),
		"card": build_card(doc, context),
		"resolved_via": resolved_via,
	}

	# `dedupe` is what drops the message when the project manager is also the
	# person who just moved the document here. That never happens on a normal
	# approval, but a direct edit by them would otherwise bounce straight back.
	return {"messages": common.dedupe([message], actor), "skips": []}


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------


def resolve_project_manager(doc, new_state):
	"""Return (user link or email, source, None), or (None, None, skip reason).

	The three sources are tried nearest-the-document first; see the module
	docstring for why there are three. `source` names the one that answered and
	is written to Cliq Message Log, so a recipient who looks wrong can be traced
	back to the exact field that produced them instead of being argued about.

	**Exactly one person, always.** Every branch returns on the first match, so
	a project mapped in two places cannot produce two messages -- the nearest
	source wins and the rest are never consulted.
	"""
	# 1. Project Head Name on this Material Request. This is the field the
	#    form fills with the project manager's address and the one to trust:
	#    across 17,700 documents it is the only one that ever holds an email.
	#    `custom_project_lead_name` never does -- not once -- which is why it
	#    is second and not first.
	for field in ("custom_project_head_name", "custom_project_lead_name"):
		value = (doc.get(field) or "").strip()
		if "@" in value:
			return value, f"Material Request.{field}", None

	project = (doc.get("project") or "").strip()
	if project:
		row = frappe.db.get_value(
			"Project",
			project,
			["custom_project_lead", "custom_project_lead_name"],
			as_dict=True,
		) or {}

		# 2. The project's own Project Lead -- a Link to Employee, so it
		#    resolves to a real address through common.resolve_email.
		employee = (row.get("custom_project_lead") or "").strip()
		if employee:
			return employee, f"Project {project}.custom_project_lead", None

		# Same free-text field one level up, in case the document's copy was
		# never fetched.
		lead = (row.get("custom_project_lead_name") or "").strip()
		if "@" in lead:
			return lead, f"Project {project}.custom_project_lead_name", None

	# 3. Employee Function, the route this handler used before. The project
	#    must match exactly: a row for a different project is a different
	#    manager, and matching loosely here is how the wrong person gets told.
	employee_function = get_employee_function(doc)
	if employee_function:
		for row in employee_function.project_list or []:
			if (row.projects or "").strip() == project and row.project_head:
				return (
					row.project_head,
					f"Employee Function {employee_function.name}.project_head[{project}]",
					None,
				)

	return None, None, SKIPS["no_project_manager"].format(
		mr=doc.name,
		state=new_state,
		project=project or "(not set)",
		ef=employee_function.name if employee_function else "(not set)",
	)


def get_employee_function(doc):
	"""The document's Employee Function, or None.

	Checked for existence first: `custom_employee_function` is a Link, but
	older rows hold codes for functions that have since been renamed or
	deleted, and `get_cached_doc` on a missing name raises.
	"""
	code = (doc.get("custom_employee_function") or "").strip()
	if not code or not frappe.db.exists("Employee Function", code):
		return None
	return frappe.get_cached_doc("Employee Function", code)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def base_context(doc, new_state) -> dict:
	"""The three values the wording and the card actually read.

	It used to carry eleven -- creator, actor, approver, items, required_by and
	more -- left over from when the message was a paragraph. The card reads its
	rows straight off the document through `CARD_ROWS`, so each of those was a
	lookup performed on every notification and then thrown away.
	"""
	return {
		"mr": doc.name,
		"url": common.form_url(DOCTYPE, doc.name),
		"reason": reason_for(doc, new_state),
		# Not wording -- the Status row reads it. It is the state the card is
		# being built *for*, which is what lets one function build both the
		# message that goes out on NOTIFY_STATE and the corrected card that
		# replaces it on approval.
		"state": new_state,
	}


def build_card(doc, context) -> dict:
	"""The structured half of the message: the document, as a Cliq card.

	`slides` of type `table` renders the two-column grid. The alternative,
	`label`, renders the same pairs as a vertical list without a header row;
	both were sent to a live bot and compared, and the table was chosen.

	An empty value is dropped rather than shown blank. A row that says
	"Project Description" and nothing else is noise, and on a narrow screen
	every dropped row is one more real row visible without scrolling.
	"""
	field, value_header = CARD_HEADERS

	rows = []
	for label, fieldname in CARD_ROWS:
		value = card_value(doc, fieldname)
		if value:
			rows.append({field: label, value_header: value})

	# Not in the reference card, and added because the approver is the one
	# person who benefits from it: at this point the remark is whatever the
	# previous approver wrote on the way past. It appears only when there is
	# one, so the card is unchanged when there is not.
	if context.get("reason"):
		rows.append({field: "Remark", value_header: context["reason"]})

	# Only ever set by `api.material_request_action`, to say why a press did
	# not do what it looked like it would -- the document had already moved,
	# or the person pressing was not the one being asked.
	if context.get("note"):
		rows.append({field: "Note", value_header: context["note"]})

	# Last, and appended after the Remark on purpose: it is the one row that is
	# true of the card rather than of the document, so it reads as the table's
	# closing line rather than as another field of the Material Request.
	if SHOW_STATUS_ROW:
		rows.append(
			{
				field: STATUS_LABEL,
				value_header: STATUS_BY_STATE.get(context.get("state"), STATUS_PENDING),
			}
		)

	return {
		# No `title` on the card. Cliq renders it as a line of its own directly
		# above `text`, and `text` already opens with the document name -- the
		# id appeared twice, stacked, which is the first thing a reader sees.
		# The theme stays: it is what makes this render as a card at all.
		"card": {"theme": "modern-inline"},
		# No slide title either. It used to repeat the document's own title
		# field above the table, which is a line the table does not need.
		"slides": [
			{
				"type": "table",
				"data": {"headers": list(CARD_HEADERS), "rows": rows},
			}
		],
		# Always present, and empty rather than absent when the state has no
		# use for it. A send drops an empty list on its own -- `build_request`
		# only copies keys that hold something -- but an edit has to say the
		# word: a PUT that simply omits `buttons` leaves the delivered
		# message's own button sitting there, which is the opposite of what
		# dropping it means.
		"buttons": buttons(context),
	}


def buttons(context) -> list:
	"""What the approver can do from the message, given where it stands.

	Pending and configured for it: Approve, Reject, and the link. Pending
	without that configuration: the link alone, which is the behaviour this
	app had before any of it existed. Decided: whatever the state allows,
	which for an approval is nothing.
	"""
	if context.get("state") in BUTTONLESS_STATES:
		return []

	out = []
	if context.get("state") == NOTIFY_STATE:
		out += action_buttons(context)

	out.append(
		{
			"label": BUTTON_LABEL.format(**context)[:BUTTON_LABEL_LIMIT],
			"type": "+",
			"action": {
				"type": "open.url",
				"data": {"web": context["url"]},
			},
		}
	)
	return out


def action_buttons(context) -> list:
	"""Approve and Reject, or nothing at all.

	Nothing at all when Cliq Settings does not name the function behind them.
	A button that invokes a function Cliq cannot resolve fails in the
	approver's face when they press it, and a card with no button at least
	still carries the link -- so an unconfigured site degrades to what it had
	rather than to something broken.
	"""
	settings = get_settings()
	name = (settings.get("function_name") or "").strip()
	owner = (settings.get("function_owner") or "").strip()
	if not (name and owner):
		return []

	return [
		{
			"label": label,
			"type": outline,
			# 'approve::MAT-MR-2026-03958' -- the press and the document it is
			# about, in the one field Cliq will carry. See
			# ACTION_KEY_SEPARATOR for why there is nowhere better.
			"key": f"{key}{ACTION_KEY_SEPARATOR}{context['mr']}",
			"action": {
				"type": "invoke.function",
				"data": {"name": name, "owner": owner},
			},
		}
		for label, key, _action, outline in ACTION_BUTTONS
	]


def follow_link(doc, path: str) -> str:
	"""Read a value off a record this document links to, one dot per hop.

	'custom_employee_function.reporting_head.full_name' reads the Link field on
	the Material Request, then `reporting_head` off that Employee Function, then
	`full_name` off the User that names. Every hop's target doctype comes from
	the field's own `options`, so no doctype is spelled out here and a renamed
	link target needs no change in this file.

	Why the walk exists at all: what is stored is a code or a login.
	'VP-LTP-QCT-001' and 'ganesh.dubey@microcrispr.com' both identify a record
	and neither says anything to the person being asked to approve -- they want
	'Ganesh Shanker Dubey'. Note that the function's own `function_name` is not
	what is wanted either: it is the department, which the approver already
	knows.

	**Only the last hop falls back; an earlier one that leads nowhere drops the
	row.** The difference is what the fallback would be read as. 17 of this
	site's 115 functions have no `function_head_name`, and for those the walk
	ends holding 'VP-LTP-QCT-001' -- poor, but it names the function whose head
	was wanted and the approver can open it. One hop earlier, the same code
	would appear under *Reporting Head*, where it names nothing at all and
	reads as that person's id. 106 of those 115 have no `reporting_head`, so
	that is the common case, not the corner: the row is dropped instead.
	"""
	fields = path.split(".")
	doctype = doc.doctype
	value = doc.get(fields[0])

	for previous, field in zip(fields, fields[1:]):
		last_hop = field == fields[-1]

		link = str(value or "").strip()
		if not link:
			return ""

		meta_field = frappe.get_meta(doctype).get_field(previous)
		target = meta_field.options if meta_field and meta_field.fieldtype == "Link" else None
		if not target:
			return link if last_hop else ""

		# Not `get_cached_doc`: that raises on a name that no longer exists, and
		# older documents do hold codes for functions since renamed or deleted.
		# `get_value` answers None, and the walk ends on the code it was holding.
		next_value = frappe.db.get_value(target, link, field)
		if not next_value:
			return link if last_hop else ""

		doctype, value = target, next_value

	return str(value or "").strip()


def card_value(doc, fieldname) -> str:
	"""One card row's value, as a string Cliq can render.

	`fieldname` may be a tuple, in which case the first field with a value
	wins. That is how 'Created By' prefers the readable name over the link
	over `owner`, without three rows or three special cases.

	It may also be a dotted path, which is read off the records this document
	links to rather than off the document; see `follow_link`. A `PERSON_FIELDS`
	field is swapped for the name behind the address.

	Dates arrive as `datetime.date` and would otherwise serialise as
	`2026-05-21`, which is not the format this site shows anywhere else.
	"""
	if isinstance(fieldname, tuple):
		for candidate in fieldname:
			value = card_value(doc, candidate)
			if value:
				return value
		return ""

	if "." in fieldname:
		value = follow_link(doc, fieldname)
	else:
		value = doc.get(fieldname)

	if value in (None, ""):
		return ""

	if fieldname == "transaction_date":
		return formatdate(value)

	if fieldname in PERSON_FIELDS:
		value = frappe.db.get_value("User", str(value).strip(), "full_name") or value

	# Collapse runs of whitespace: several of these values are typed by hand
	# and carry double spaces, which the table renders literally.
	value = " ".join(str(value).split())

	if CARD_VALUE_LIMIT and len(value) > CARD_VALUE_LIMIT:
		value = value[: CARD_VALUE_LIMIT - 1].rstrip() + "…"

	return value


def reason_for(doc, new_state) -> str:
	"""The remark attached to this action, or "" if there is none.

	Read from Material Request Workflow Log rather than `doc.custom_remark`,
	because mr_close.material_request_double_approval blanks that field during
	before_save -- by the time this runs it is always empty.
	"""
	remark = frappe.db.get_value(
		"Material Request Workflow Log",
		{"material_request": doc.name, "workflow_state": new_state},
		"remarks",
		order_by="action_datetime desc",
	)

	remark = (remark or doc.get("custom_remark") or "").strip()
	# The logger falls back to writing the state name when there is no remark;
	# echoing that back as a reason says nothing.
	if not remark or remark == new_state:
		return ""
	return remark
