app_name = "eve_agent"
app_title = "Eve Agent"
app_publisher = "aswin"
app_description = "ERP graph agent backed by FalkorDB"
app_email = "aswin@softservedweb.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "eve_agent",
# 		"logo": "/assets/eve_agent/logo.png",
# 		"title": "Eve Agent",
# 		"route": "/eve_agent",
# 		"has_permission": "eve_agent.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/eve_agent/css/eve_agent.css"
# app_include_js = "/assets/eve_agent/js/eve_agent.js"
app_include_js = "/assets/eve_agent/dist/eve_chat.js"
app_include_css = "/assets/eve_agent/dist/eve_chat.css"
boot_session = "eve_agent.boot.boot_session"

# include js, css files in header of web template
# web_include_css = "/assets/eve_agent/css/eve_agent.css"
# web_include_js = "/assets/eve_agent/js/eve_agent.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "eve_agent/public/scss/website"

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
# app_include_icons = "eve_agent/public/icons.svg"

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
# 	"methods": "eve_agent.utils.jinja_methods",
# 	"filters": "eve_agent.utils.jinja_filters"
# }

# Installation
# ------------

after_install = "eve_agent.install.after_install"
after_migrate = ["eve_agent.install.after_migrate"]

# Keep the data graph current: every synced record change queues an incremental
# upsert into the active graph, a 5-minute sweep catches changes made without a
# hook, and a nightly blue/green rebuild heals the rest (see eve_agent/sync/refresh.py).
doc_events = {
	"*": {
		"on_update": "eve_agent.sync.refresh.queue_record",
		"on_submit": "eve_agent.sync.refresh.queue_record",
		"on_cancel": "eve_agent.sync.refresh.queue_record",
		"on_update_after_submit": "eve_agent.sync.refresh.queue_record",
		"on_trash": "eve_agent.sync.refresh.queue_record",
		"after_rename": "eve_agent.sync.refresh.queue_record",
	}
}

scheduler_events = {
	"cron": {
		"*/5 * * * *": ["eve_agent.sync.refresh.sweep"],
		"0 2 * * *": ["eve_agent.sync.refresh.nightly_rebuild"],
	}
}

# Log Settings can prune the Cypher log.
default_log_clearing_doctypes = {"Eve Cypher Log": 90}

# Uninstallation
# ------------

# before_uninstall = "eve_agent.uninstall.before_uninstall"
# after_uninstall = "eve_agent.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "eve_agent.utils.before_app_install"
# after_app_install = "eve_agent.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "eve_agent.utils.before_app_uninstall"
# after_app_uninstall = "eve_agent.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "eve_agent.build.after_build"

# To hook into the build process of other apps
# The list of apps being built is passed as an argument

# after_app_build = "eve_agent.build.after_app_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "eve_agent.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["eve_agent.search.awesomebar_results"]

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
# 		"eve_agent.tasks.all"
# 	],
# 	"daily": [
# 		"eve_agent.tasks.daily"
# 	],
# 	"hourly": [
# 		"eve_agent.tasks.hourly"
# 	],
# 	"weekly": [
# 		"eve_agent.tasks.weekly"
# 	],
# 	"monthly": [
# 		"eve_agent.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "eve_agent.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "eve_agent.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "eve_agent.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["eve_agent.utils.before_request"]
# after_request = ["eve_agent.utils.after_request"]

# Job Events
# ----------
# before_job = ["eve_agent.utils.before_job"]
# after_job = ["eve_agent.utils.after_job"]

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
# 	"eve_agent.auth.validate"
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

