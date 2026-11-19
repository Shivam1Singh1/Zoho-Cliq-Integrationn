# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

"""Delivery: everything between "here is a message" and "Zoho has it".

Handlers in `notifications/` decide *who* hears *what*. This module owns the
rest, and nothing above it needs to know any of it:

    queue_message()  write a log row and hand off to a worker
    deliver()        the worker -- post it, judge the reply, retry or finish
    update_sent_card()  rewrite a delivered message instead of sending another
    build_request()  the exact URL and JSON body Zoho receives
    log_skipped()    record a message that could not be addressed

Two rules shape the whole file. **A save is never blocked**: `queue_message`
only writes a row and enqueues, so the HTTP call happens at least one hop
away from the user's transaction. **A log row is the truth**: every message,
sent or not, leaves one, and that row is also the duplicate guard.
"""

import json

import frappe
from frappe.utils import cint, now_datetime

# Worth another try -- the far end was busy, throttled or briefly
# unavailable. Any other 4xx is a bad request and fails identically on retry.
RETRYABLE_STATUS = (408, 429, 500, 502, 503, 504)

# Statuses meaning "this message exists". The duplicate guard matches only
# these, so a Failed or Skipped row never blocks a later genuine attempt.
LIVE_STATUSES = ("Queued", "Sent", "Dry Run", "Undelivered")

SKIPPED_NO_RECIPIENT = "Skipped - No Recipient"
SKIPPED_NOT_ALLOWED = "Skipped - Recipient Not Allowed"

DIRECT = "Direct Message"
CHANNEL = "Channel"
BOTH = "Both"


def get_settings():
	"""Cliq Settings, from cache.

	Read on every queued message and every delivery attempt, so it must not be
	a query. `on_update` of the Single clears the cache, which is what makes a
	changed setting take effect on the next message rather than the next
	restart.
	"""
	return frappe.get_cached_doc("Cliq Settings")


def get_modes(settings=None) -> list[str]:
	"""The delivery routes one message should take.

	`Both` is the only setting producing more than one, and it is why callers
	loop over modes instead of sending once: a DM and a channel post are two
	separate HTTP calls with two separate log rows.
	"""
	settings = settings or get_settings()
	target_type = settings.target_type or DIRECT
	if target_type == BOTH:
		return [DIRECT, CHANNEL]
	return [target_type]


def resolve_identity(user_email: str) -> str | None:
	"""An ERPNext address mapped to whatever Cliq knows the person by.

	Usually the two are the same and `user_id` passes the address straight
	through. A site whose staff log into Cliq under a different address sets
	`identity_field` to `company_email`, and the Employee record is the bridge.

	Falls back to the original address when no Employee matches: a wrong
	address fails loudly at Zoho, an empty one fails silently here.
	"""
	user_email = (user_email or "").strip()
	if not user_email:
		return None

	settings = get_settings()
	field = settings.identity_field or "user_id"

	if field == "user_id":
		return user_email.lower()

	mapped = frappe.db.get_value(
		"Employee",
		{"user_id": user_email, "status": "Active"},
		field,
	)

	mapped = (mapped or "").strip()
	return (mapped or user_email).lower()


def allowed_domains(settings) -> list[str]:
	"""The allowlist, parsed. Accepts commas or newlines, with or without @."""
	raw = (settings.allowed_email_domains or "").replace("\n", ",")
	return [d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip()]


def is_allowed_recipient(settings, address: str | None) -> bool:
	"""Guard against messaging anyone outside the configured domains.

	The roles and user lists in this site carry support accounts, consultants
	and test logins. An empty setting allows everything, which is the only way
	to turn the guard off.
	"""
	domains = allowed_domains(settings)
	if not domains:
		return True

	address = (address or "").strip().lower()
	return any(address.endswith("@" + domain) for domain in domains)


def resolve_target(settings, mode: str, recipient_user: str | None) -> str | None:
	"""Where the message actually goes -- a channel name, or an address.

	`test_redirect` is applied here and nowhere else, deliberately: it has to
	override the destination *after* the recipient is resolved, so the log
	keeps the person the document named while delivery goes somewhere safe.

	It must be empty in production. Left set, every approver's message lands
	on one person and the log still reads Sent.
	"""
	if mode == CHANNEL:
		channel = (settings.channel_unique_name or "").strip()
		if not channel:
			frappe.log_error(
				title="Cliq Settings incomplete",
				message=(
					"Cliq is set to post into a channel but no Channel Unique Name "
					"is set, so nothing can be sent."
				),
			)
			return None
		return channel

	target = resolve_identity(recipient_user)
	if not target:
		return None

	redirect = (settings.test_redirect or "").strip()
	return redirect.lower() if redirect else target


def queue_message(
	recipient_user: str | None,
	subject: str,
	message: str,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	trigger_state: str | None = None,
	modes: list[str] | None = None,
	card: dict | None = None,
	resolved_via: str | None = None,
) -> list[str]:
	"""Write a log row per delivery mode and hand each to a worker.

	Returns the log names created -- empty if the integration is off, the
	target would not resolve, the recipient is outside the allowlist, or the
	duplicate guard has seen this message already. None of those are errors,
	and each leaves its own trace.

	Runs inside the caller's transaction and never touches the network, so a
	slow or dead Cliq cannot delay a user's save.
	"""
	settings = get_settings()
	if not settings.enabled:
		return []

	log_names = []
	for mode in modes or get_modes(settings):
		target = resolve_target(settings, mode, recipient_user)
		if not target:
			continue

		# A channel name is not an address, so the allowlist applies to direct
		# messages only. The check is on the resolved target, which means a
		# Test Redirect pointing outside the allowed domains is caught too.
		if mode == DIRECT and not is_allowed_recipient(settings, target):
			log_skipped(
				reason=(
					f"{target} is outside Allowed Email Domains "
					f"({', '.join(allowed_domains(settings))}), so no message was sent."
				),
				status=SKIPPED_NOT_ALLOWED,
				reference_doctype=reference_doctype,
				reference_name=reference_name,
				trigger_state=trigger_state,
				recipient_user=recipient_user,
				recipient_cliq=target,
				subject=subject,
				message=message,
			)
			continue

		if _already_raised(reference_doctype, reference_name, trigger_state, target):
			continue

		log = frappe.get_doc(
			{
				"doctype": "Cliq Message Log",
				"status": "Dry Run" if settings.dry_run else "Queued",
				"delivery_mode": mode,
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
				"trigger_state": trigger_state,
				"recipient_user": recipient_user,
				"resolved_via": resolved_via,
				"recipient_cliq": target,
				"subject": subject,
				"message": message,
				"card_data": json.dumps(card, indent=2) if card else None,
				"attempts": 0,
				# Dry Run still records exactly what would have gone out, so a
				# configuration can be checked without a live bot.
				"response": _dry_run_preview(
					settings, target, recipient_user, subject, message, mode, card
				)
				if settings.dry_run
				else None,
			}
		)
		log.insert(ignore_permissions=True)
		log_names.append(log.name)

		if settings.dry_run:
			continue

		frappe.enqueue(
			"zoho_cliq_integration.cliq.client.deliver",
			queue="short",
			enqueue_after_commit=True,
			job_id=f"cliq-msg::{log.name}",
			log_name=log.name,
		)

	return log_names


def _dry_run_preview(settings, target, recipient_user, subject, message, mode, card=None) -> str:
	"""The whole request, rendered and stored instead of sent.

	This is what makes Dry Run worth having: the row carries the exact URL and
	payload, so a configuration can be proved correct -- including who it would
	have reached -- before a live bot exists.
	"""
	url, payload = build_request(settings, target, subject, message, mode, card)
	return json.dumps(
		{
			"dry_run": True,
			"mode": mode,
			"method": "POST",
			"url": url,
			"params": {"zapikey": "<read from Cliq Settings at send time>"},
			"payload": payload,
			"intended_recipient": recipient_user,
			"resolved_target": target,
		},
		indent=2,
	)


def log_skipped(
	reason: str,
	status: str = SKIPPED_NO_RECIPIENT,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	trigger_state: str | None = None,
	recipient_user: str | None = None,
	recipient_cliq: str | None = None,
	subject: str | None = None,
	message: str | None = None,
) -> str | None:
	"""Record a message that could not be addressed.

	An unmapped approver is a configuration gap, not a failure, so it belongs
	in the message log beside the messages that did go out. The Error Log stays
	reserved for things that actually broke.
	"""
	settings = get_settings()
	if not settings.enabled:
		return None

	if frappe.db.exists(
		"Cliq Message Log",
		{
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"trigger_state": _unset_or(trigger_state),
			"recipient_cliq": _unset_or(recipient_cliq),
			"status": status,
		},
	):
		return None

	log = frappe.get_doc(
		{
			"doctype": "Cliq Message Log",
			"status": status,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"trigger_state": trigger_state,
			"recipient_user": recipient_user,
			"recipient_cliq": recipient_cliq,
			"subject": subject,
			"message": message,
			"attempts": 0,
			"error": reason,
		}
	)
	log.insert(ignore_permissions=True)
	return log.name


def _already_raised(reference_doctype, reference_name, trigger_state, target):
	"""Has this exact message already been raised?

	The key is (document, transition, target). One workflow transition saves
	the document several times -- seven to eight hook invocations were seen for
	a single approval -- and `frappe.enqueue(deduplicate=True)` only collapses
	jobs still waiting in the queue. This catches the rest, and is what makes
	the worker safe to run twice.
	"""
	if not (reference_doctype and reference_name):
		return False

	return bool(
		frappe.db.exists(
			"Cliq Message Log",
			{
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
				"trigger_state": _unset_or(trigger_state),
				"recipient_cliq": target,
				"status": ("in", LIVE_STATUSES),
			},
		)
	)


def _card_of(log):
	"""The stored structured payload, or None.

	A log row written before cards existed, or by a handler that sends plain
	text, has nothing here -- and a row whose JSON will not parse must not
	stop delivery, so it degrades to a plain-text send rather than raising.
	"""
	raw = (log.get("card_data") or "").strip()
	if not raw:
		return None
	try:
		return json.loads(raw)
	except Exception:
		return None


def _unset_or(value):
	"""A filter value that also matches NULL.

	`{"field": None}` means "equals NULL" on some Frappe paths and is dropped
	on others; `("is", "not set")` is unambiguous. The guard needs it to match
	rows whose trigger_state or target was never set.
	"""
	return value if value else ("is", "not set")


def deliver(log_name: str):
	"""The worker: post one log row to Zoho and record what happened.

	Re-entrant on purpose. Only `Queued` and `Failed` rows are acted on, so a
	duplicated job, a manual re-run, or a retry racing itself cannot send the
	same message twice.

	Settings are re-read here rather than trusted from queue time: a message
	queued before someone disabled the integration must not go out after.
	"""
	log = frappe.get_doc("Cliq Message Log", log_name)
	if log.status not in ("Queued", "Failed"):
		return

	settings = get_settings()
	if not settings.enabled:
		_finish(log, "Skipped", error="Cliq Settings is disabled.")
		return

	if settings.dry_run:
		_finish(log, "Dry Run")
		return

	mode = log.delivery_mode or get_modes(settings)[0]
	attempts = cint(log.attempts) + 1
	max_attempts = cint(settings.max_attempts) or 3

	try:
		status_code, body = _post(
			settings, log.recipient_cliq, log.subject, log.message, mode, _card_of(log)
		)
	except Exception as exc:
		if attempts < max_attempts:
			_retry(log, attempts, str(exc))
		else:
			_finish(log, "Failed", attempts=attempts, error=str(exc))
		return

	if 200 <= status_code < 300:
		if mode == CHANNEL or _resolved_anyone(body):
			# Kept because an edit has to be addressed to a message, and this
			# reply is the only place Zoho ever offers one. Posting as a bot
			# it does not, so this is usually empty -- see `update_sent_card`.
			_remember_message(log, body)
			_finish(log, "Sent", attempts=attempts, response=body)
		else:
			_finish(
				log,
				"Undelivered",
				attempts=attempts,
				error=(
					f"Cliq accepted the message but resolved no user for "
					f"'{log.recipient_cliq}'. The address has no Cliq account, "
					f"or the bot may not DM it."
				),
				response=body,
			)
		return

	error = f"HTTP {status_code}: {body}"
	if status_code in RETRYABLE_STATUS and attempts < max_attempts:
		_retry(log, attempts, error)
	else:
		_finish(log, "Failed", attempts=attempts, error=error, response=body)


def _resolved_anyone(body: str) -> bool:
	"""Did Zoho match the address to a real account?

	An unparseable body returns True -- optimistic by design. Treating a reply
	we failed to read as a delivery failure would mark working messages
	Undelivered on any future change to Zoho's response shape.
	"""
	try:
		return bool(json.loads(body or "{}").get("user_ids"))
	except Exception:
		return True


def _remember_message(log, body: str):
	"""Store what Zoho gave us to address this message by, later.

	The id is not where the documentation suggests. A bot post answers with

	    {"user_ids": ["60062744041"],
	     "message_details": {"60062744041": {"chat_id": "CT_120...-B2",
	                                         "message_id": "1795071752472%2055609997592"}},
	     "users": ["60062744041"]}

	-- one entry per resolved user, keyed by their ZUID, and only when
	`sync_message` was sent. The flat `message_id` is read first anyway
	because that is the shape the OAuth endpoints return.

	The id is left percent-encoded exactly as Zoho wrote it. It contains a
	space, and that space is already escaped for the URL it will be pasted
	into; decoding it here would mean re-encoding it there.
	"""
	try:
		payload = json.loads(body or "{}")
	except Exception:
		return

	if not isinstance(payload, dict):
		return

	message_id = payload.get("message_id") or payload.get("id")
	chat_id = payload.get("chat_id") or payload.get("chid")

	# One recipient per message here, so the first entry is the message. A
	# multi-recipient send would need a row each to stay addressable.
	for details in (payload.get("message_details") or {}).values():
		if not isinstance(details, dict):
			continue
		message_id = message_id or details.get("message_id")
		chat_id = chat_id or details.get("chat_id")
		break

	# Written straight to the row rather than set on the document. `_finish`
	# runs next and writes only the fields it was given, so anything left in
	# memory here is dropped -- which is how the first version of this lost
	# every id it read.
	values = {}
	if message_id:
		values["message_id"] = str(message_id)
	if chat_id:
		values["chat_id"] = str(chat_id)
	if values:
		log.db_set(values, update_modified=False)


def update_sent_card(
	reference_doctype: str,
	reference_name: str,
	card: dict,
	trigger_state_like: str,
	subject: str | None = None,
	message: str | None = None,
) -> list[str]:
	"""Rewrite messages already delivered for this document, in place.

	**This never sends anything.** It edits what is already on the reader's
	screen, or it records why it could not -- and either way no second message
	appears, which is the whole reason it exists.

	`trigger_state_like` picks which of a document's messages to rewrite, by
	matching the transition that produced them. A document that has been round
	the workflow twice has two rows, and only the one now being superseded
	should change.
	"""
	rows = frappe.get_all(
		"Cliq Message Log",
		filters={
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"trigger_state": ("like", trigger_state_like),
			"status": ("in", LIVE_STATUSES),
		},
		pluck="name",
		order_by="creation desc",
	)

	updated = []
	for name in rows:
		log = frappe.get_doc("Cliq Message Log", name)
		if _update_one(log, card, subject, message):
			updated.append(name)
	return updated


def _update_one(log, card: dict, subject: str | None, message: str | None) -> bool:
	"""Edit one delivered message, or write down why it stayed as it was.

	The note goes on the row rather than into the Error Log. A message that
	cannot be edited is a gap in the configuration, exactly like a recipient
	that cannot be addressed, and the row is where this app keeps those.

	The stored card is refreshed whichever way it goes: it is what a resend or
	a dry-run preview would use, and leaving a card behind that says Pending
	for a document that is approved would be a second, quieter wrong answer.
	"""
	log.db_set("card_data", json.dumps(card, indent=2), update_modified=False)

	settings = get_settings()
	note = _cannot_update(settings, log)
	if note:
		log.db_set("update_note", note, update_modified=False)
		return False

	try:
		status_code, body = _put_card(settings, log, card, subject, message)
	except Exception as exc:
		log.db_set("update_note", f"Update failed: {exc}", update_modified=False)
		return False

	if 200 <= status_code < 300:
		log.db_set("update_note", f"Updated in place at {now_datetime()}.", update_modified=False)
		return True

	log.db_set("update_note", f"Update refused: HTTP {status_code}: {body}", update_modified=False)
	return False


def _cannot_update(settings, log) -> str | None:
	"""The reason this message cannot be edited, or None if it can.

	Each of these is a wall rather than a failure to try, and each names what
	would have to change to clear it -- the note lands on the log row a reader
	is already looking at when they ask why the card still says Pending.
	"""
	if not log.message_id:
		return (
			"This message was sent before the id was being kept, so there is "
			"nothing to address an edit to. Messages sent from now on carry "
			"one. The card was left as sent, and nothing was sent again."
		)

	if log.delivery_mode == CHANNEL and not log.chat_id:
		return (
			"A channel post carries no chat id in Zoho's reply, so there is "
			"nothing to address an edit to. The message was left as sent."
		)

	refresh_token = settings.get_password("oauth_refresh_token", raise_exception=False)
	if not (settings.get("oauth_client_id") and refresh_token):
		return (
			"Editing a sent message needs an OAuth token carrying the "
			"ZohoCliq.Messages.UPDATE scope. A zapikey is refused with HTTP "
			"401 here -- tested against this bot, not assumed. Fill in the "
			"In-Place Updates section of Cliq Settings. Nothing was sent."
		)

	return None


def _access_token(settings) -> str:
	"""A short-lived access token, refreshed when it has run out.

	Cached rather than fetched per call: Zoho rate-limits token refreshes far
	more tightly than it limits messages, and a burst of approvals would spend
	the quota on tokens. The cached life is cut two minutes short of Zoho's
	own, because a token that expires in flight comes back as an
	authentication failure and reads like a bad refresh token.
	"""
	cached = frappe.cache().get_value("cliq_access_token")
	if cached:
		return cached

	import requests

	response = requests.post(
		f"https://{_accounts_domain(settings)}/oauth/v2/token",
		params={
			"refresh_token": settings.get_password("oauth_refresh_token", raise_exception=False),
			"client_id": settings.get("oauth_client_id"),
			"client_secret": settings.get_password("oauth_client_secret", raise_exception=False),
			"grant_type": "refresh_token",
		},
		timeout=cint(settings.timeout) or 10,
	)
	payload = response.json()

	token = payload.get("access_token")
	if not token:
		raise Exception(f"no access token in Zoho's reply: {payload}")

	frappe.cache().set_value(
		"cliq_access_token", token, expires_in_sec=cint(payload.get("expires_in") or 3600) - 120
	)
	return token


def _accounts_domain(settings) -> str:
	"""Where tokens are refreshed, following the API domain by default.

	The data centre has to match. A token refreshed at accounts.zoho.com is
	not valid against cliq.zoho.in, and that failure reads as a bad refresh
	token rather than as the wrong continent.
	"""
	configured = (settings.get("accounts_domain") or "").strip()
	if configured:
		return configured
	return (settings.api_domain or "cliq.zoho.com").replace("cliq.", "accounts.", 1)


def _put_card(settings, log, card: dict, subject: str | None, message: str | None):
	"""The edit itself: v3, because v2 can only rewrite `text`.

	The status this app wants to change lives in a table slide, and the v2
	edit API accepts `text` and nothing else -- it would rewrite the line
	above the card while the card under it went on saying Pending. v3 accepts
	`card`, `slides` and `buttons`, which is the whole reason the edit is on
	v3 while sending stays on v2.

	Two details are the API's, not choices. `text` is **mandatory** -- a body
	carrying only slides comes back `param_missing` -- so it is always sent,
	rebuilt from the same subject the message went out with. And
	`bot_unique_name` must *not* be in the query: this endpoint answers
	`extra_param_found` and refuses the whole request.

	Parameters are validated before credentials, which is worth knowing when
	reading a failure here: a 400 about a parameter says nothing about whether
	the token would have been accepted.
	"""
	import requests

	url = (
		f"https://{settings.api_domain}/api/v3/chats/"
		f"{log.chat_id or log.recipient_cliq}/messages/{log.message_id}"
	)

	# Mandatory, and sent first so a card with nothing else to say still
	# forms a valid request.
	payload = {
		"notify_edit": False,
		"text": build_request(settings, "", subject or "", message or "")[1]["text"],
	}
	for key in ("card", "slides", "buttons"):
		# `is not None`, not truthiness: an empty `buttons` list is an
		# instruction to remove the button that is on the message now, and
		# testing truthiness would silently drop that instruction and leave it
		# there. A key the card never set is still omitted.
		if card.get(key) is not None:
			payload[key] = card[key]

	response = requests.put(
		url,
		headers={"Authorization": f"Zoho-oauthtoken {_access_token(settings)}"},
		json=payload,
		timeout=cint(settings.timeout) or 10,
	)
	return response.status_code, response.text[:4000]


def build_request(
	settings, target: str, subject: str, message: str, mode: str = DIRECT, card: dict | None = None
):
	"""The exact URL and JSON body Zoho would receive, without sending it.

	`card` is the structured half of a Cliq message -- any of `card`, `slides`
	and `buttons` -- and is merged in as top-level keys beside `text`. It is
	optional on purpose: a handler that has nothing structured to say sends
	plain text and the payload is unchanged from before cards existed.

	`text` is still sent even when a card is present. Cliq uses it for the
	line above the card, and it is also what a push notification and the chat
	list preview show, where the card is not rendered at all.
	"""
	if subject and message:
		text = f"*{subject}*\n\n{message}"
	elif subject:
		# A card carries the detail, so the body is empty and the subject is
		# the whole line above it -- bold, because it is a heading there.
		text = f"*{subject}*"
	else:
		text = message

	if mode == CHANNEL:
		url = f"https://{settings.api_domain}/api/v2/channelsbyname/{target}/message"
		payload = {"text": text}
	else:
		url = (
			f"https://{settings.api_domain}/api/v2/bots/"
			f"{settings.bot_unique_name}/message"
		)
		# `sync_message` is what makes Zoho answer with the id of the message
		# it just created, under `message_details`. Without it the reply is
		# only the list of users it resolved, and a message with no id can
		# never be edited afterwards -- which is the whole of `update_sent_card`.
		#
		# Direct messages only. The channel endpoint rejects parameters it does
		# not know with `extra_param_found`, and a notification that fails to
		# send is a far worse trade than a channel post that cannot be edited.
		payload = {"text": text, "userids": target, "sync_message": True}

	for key in ("card", "slides", "buttons"):
		if card and card.get(key):
			payload[key] = card[key]

	return url, payload


def _post(settings, target: str, subject: str, message: str, mode: str = DIRECT, card: dict | None = None):
	"""The one place this app talks to the network.

	`requests` is imported here rather than at module scope so that importing
	this module -- which `hooks.py` does on every request -- costs nothing.

	The body is truncated to 4000 characters because it is stored on the log
	row, and a Zoho error page can run to megabytes.
	"""
	import requests

	zapikey = settings.get_password("zapikey", raise_exception=False)
	url, payload = build_request(settings, target, subject, message, mode, card)

	response = requests.post(
		url,
		params={"zapikey": zapikey},
		json=payload,
		timeout=cint(settings.timeout) or 10,
	)
	return response.status_code, response.text[:4000]


def _retry(log, attempts, error):
	"""Put the row back on the queue, with the failure recorded.

	`update_modified=False` keeps the row's timestamp honest -- a retry is not
	someone editing the log. The commit is required because the job is enqueued
	immediately and the worker reads the row from the database, not from this
	transaction.
	"""
	log.db_set(
		{"status": "Queued", "attempts": attempts, "error": error},
		update_modified=False,
	)
	frappe.db.commit()
	frappe.enqueue(
		"zoho_cliq_integration.cliq.client.deliver",
		queue="short",
		job_id=f"cliq-msg::{log.name}::retry{attempts}",
		log_name=log.name,
	)


def _finish(log, status, attempts=None, error=None, response=None):
	"""Close out a row: write the outcome, or delete it if nobody wants it.

	A successful send is deleted when `log_successful_sends` is off, which is
	how a busy site keeps the table to failures only. Everything else is kept.

	A `Failed` row is additionally written to the Error Log -- the row already
	has the detail, but a genuine delivery failure belongs where failures are
	actually looked for.
	"""
	settings = get_settings()

	# Note that this also forfeits any later edit: `update_sent_card` finds
	# messages through their log rows, so a site that does not keep the
	# successful ones has nothing left to address an edit to.
	if status == "Sent" and not settings.log_successful_sends:
		frappe.delete_doc(
			"Cliq Message Log", log.name, ignore_permissions=True, force=True
		)
		frappe.db.commit()
		return

	values = {"status": status, "error": error}
	if attempts is not None:
		values["attempts"] = attempts
	if status in ("Sent", "Dry Run"):
		values["sent_at"] = now_datetime()
	if response is not None:
		values["response"] = _pretty(response)

	log.db_set(values, update_modified=False)
	frappe.db.commit()

	if status == "Failed":
		# Retries are spent and the message is never going out. The log row
		# already carries the detail, but a genuine delivery failure belongs
		# in the Error Log too, where failures are actually looked for.
		frappe.log_error(
			title=f"Cliq: delivery failed for {log.reference_name or log.name}"[:140],
			message="\n".join(
				[
					f"log       : {log.name}",
					f"document  : {log.reference_doctype} {log.reference_name}",
					f"transition: {log.trigger_state}",
					f"mode      : {log.delivery_mode}",
					f"intended  : {log.recipient_user}",
					f"target    : {log.recipient_cliq}",
					f"attempts  : {values.get('attempts', log.attempts)}",
					f"error     : {error}",
				]
			),
		)


def _pretty(body):
	"""Re-indent a JSON reply so the log row reads well; else leave it alone."""
	try:
		return json.dumps(json.loads(body), indent=2)
	except Exception:
		return body


@frappe.whitelist()
def send_test_message(recipient: str = None, text: str = "Test message from ERPNext."):
	"""Prove the whole delivery path, synchronously, from the settings page.

	Deliberately not queued: the point is to hand the caller a result now --
	settings, identity mapping, every configured mode, and the real HTTP call.
	A green tick here is the only evidence that bot, token, domain and address
	actually work together.

	System Manager only. It sends a real message and reveals whether a given
	address resolves in Cliq.
	"""
	frappe.only_for("System Manager")
	log_names = queue_message(
		recipient_user=recipient,
		subject="ERPNext test",
		message=text,
		trigger_state="manual-test",
	)
	if not log_names:
		return {"ok": False, "message": "Nothing queued. Check Cliq Settings is enabled and configured."}

	settings = get_settings()
	if settings.dry_run:
		return {
			"ok": True,
			"logs": log_names,
			"message": "Dry Run is on: the message was rendered and logged, not sent.",
		}

	results = []
	for log_name in log_names:
		deliver(log_name)
		log = frappe.get_doc("Cliq Message Log", log_name)
		results.append(
			{
				"log": log_name,
				"mode": log.delivery_mode,
				"status": log.status,
				"target": log.recipient_cliq,
				"error": log.error,
			}
		)

	return {
		"ok": all(r["status"] == "Sent" for r in results),
		"results": results,
	}
