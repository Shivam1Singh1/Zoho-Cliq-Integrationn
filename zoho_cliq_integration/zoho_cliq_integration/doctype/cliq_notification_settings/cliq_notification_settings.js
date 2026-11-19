// Copyright (c) 2026, MicroCRISPR and contributors
// For license information, please see license.txt

// Intentionally empty. This Single is one child table of Link and Check
// fields, and every check it needs -- duplicates, and the warning for a
// doctype with no active workflow -- has to run on the server anyway, where
// the workflow is. Duplicating any of it here would only let the two drift.
