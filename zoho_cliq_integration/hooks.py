"""App manifest.

Everything this app adds to a site is declared at the bottom of this file: one
document hook on Material Request. The commented blocks in between are
Frappe's scaffolding, left as generated.
"""

app_name = "zoho_cliq_integration"
app_title = "Zoho Cliq Integration"
app_publisher = "MicroCRISPR"
app_description = "Send ERPNext document alerts as Zoho Cliq direct messages"
app_email = "shivam.singh@microcrispr.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "zoho_cliq_integration",
# 		"logo": "/assets/zoho_cliq_integration/logo.png",
# 		"title": "Zoho Cliq Integration",
# 		"route": "/zoho_cliq_integration",
# 		"has_permission": "zoho_cliq_integration.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/zoho_cliq_integration/css/zoho_cliq_integration.css"
# app_include_js = "/assets/zoho_cliq_integration/js/zoho_cliq_integration.js"

# include js, css files in header of web template
# web_include_css = "/assets/zoho_cliq_integration/css/zoho_cliq_integration.css"
# web_include_js = "/assets/zoho_cliq_integration/js/zoho_cliq_integration.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "zoho_cliq_integration/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "zoho_cliq_integration/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "zoho_cliq_integration.utils.jinja_methods",
# 	"filters": "zoho_cliq_integration.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "zoho_cliq_integration.install.before_install"
# after_install = "zoho_cliq_integration.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "zoho_cliq_integration.uninstall.before_uninstall"
# after_uninstall = "zoho_cliq_integration.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "zoho_cliq_integration.utils.before_app_install"
# after_app_install = "zoho_cliq_integration.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "zoho_cliq_integration.utils.before_app_uninstall"
# after_app_uninstall = "zoho_cliq_integration.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "zoho_cliq_integration.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"zoho_cliq_integration.tasks.all"
# 	],
# 	"daily": [
# 		"zoho_cliq_integration.tasks.daily"
# 	],
# 	"hourly": [
# 		"zoho_cliq_integration.tasks.hourly"
# 	],
# 	"weekly": [
# 		"zoho_cliq_integration.tasks.weekly"
# 	],
# 	"monthly": [
# 		"zoho_cliq_integration.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "zoho_cliq_integration.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "zoho_cliq_integration.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "zoho_cliq_integration.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["zoho_cliq_integration.utils.before_request"]
# after_request = ["zoho_cliq_integration.utils.after_request"]

# Job Events
# ----------
# before_job = ["zoho_cliq_integration.utils.before_job"]
# after_job = ["zoho_cliq_integration.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"zoho_cliq_integration.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []



# Zoho Cliq Integration
# ---------------------

# Material Request is the only doctype this app notifies on, and the only
# state it reacts to is 'Pending from Project Manager'. Messages travel one
# way -- to the project manager the document is now waiting on, and to nobody
# else. Nothing is sent back to the requester at any point in the chain.
#
# It gets a named hook rather than a wildcard because its approver is not in
# the Workflow. The workflow gates transitions by role -- 'Projects Manager'
# has 23 holders -- so the role says who *may* act, never who *must*. The one
# person a given document waits on is its project lead. See
# notifications/material_request.py.
#
# Both update events are registered. `on_update` misses a transition between
# two submitted states -- which is how an allow-on-submit workflow field
# changes, and it reaches only on_update_after_submit. The same guarded
# handler takes both, and the has_value_changed check makes the extra path
# free. There is no on_cancel: cancelling tells the requester nothing they
# did not just do themselves.
#
# Every handler lives in `notifications/`, one module per doctype, and the
# only way one runs is a line in this dict. There is one handler, because
# there is one doctype. The wildcard rule that once served every doctype in
# Cliq Notification Settings, its sender, and a Purchase Order handler have
# all been deleted rather than left dormant -- code nothing calls is code
# nobody maintains, and git still has them.
_MR_HOOK = "zoho_cliq_integration.notifications.material_request.on_workflow_state_change"

doc_events = {
	"Material Request": {
		"on_update": _MR_HOOK,
		"on_update_after_submit": _MR_HOOK,
	},
}

after_install = "zoho_cliq_integration.setup.after_install"
after_migrate = "zoho_cliq_integration.setup.after_migrate"
