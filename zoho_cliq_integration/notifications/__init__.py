# Copyright (c) 2026, MicroCRISPR and contributors
# For license information, please see license.txt

"""One module per doctype, plus the parts they share.

    common.py            resolution, rendering and queueing, shared
    material_request.py  the only handler, and the only doctype

A doctype's file holds everything about that doctype: the states it reacts to,
how it finds the person to tell, and every word it can say. Nothing about one
doctype is spread across two files, and nothing in `common.py` names a doctype.

Adding a doctype is therefore a new file here and a line in `hooks.py`, and
touching one doctype cannot disturb another. `common.py` keeps that shape even
though only one handler uses it today -- it is the seam a second one would be
added along.

Nothing in this package sends. Messages go to `cliq.client.queue_message`,
which owns delivery, retry, dry-run, the domain allowlist and the duplicate
guard.
"""
