"""Record writes: read what a DocType needs, then propose a record for the user to confirm.

Nothing here writes to Frappe. `propose_record` returns a pending action that the
widget renders as a card; clicking Create calls eve_agent.api.actions.apply, which
inserts the record under the user's own permissions. Submittable documents are
created as drafts only; submission stays a human step in the Desk.
"""

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from eve_agent.chat.common import Tool, ToolContext, filter_fields_json, result, visible
from eve_agent.chat.tools.utility import DESCRIBE_DOCTYPE

LAYOUT_TYPES = {"Section Break", "Column Break", "Tab Break", "Fold", "Button", "HTML", "Heading", "Image"}
SKIP_FIELDS = {"naming_series", "docstatus", "amended_from", "idx", "name", "owner", "creation", "modified"}
MAX_OPTIONAL = 25
RESERVED_VALUES = {"docstatus", "amended_from", "name", "owner", "creation", "modified", "modified_by"}


class RecordFormArgs(BaseModel):
	doctype: str


class ProposeRecordArgs(BaseModel):
	mode: Literal["create", "update"] = "create"
	doctype: str
	values: dict[str, Any] = Field(
		description="Field values. Child tables are lists of row dicts, e.g. items: [{item_code, qty, rate}]."
	)
	name: str | None = Field(None, description="For update: the exact record name")
	summary: str = Field(description="One line for the user, e.g. 'New customer Ashish (ashish@gmail.com)'")


def _field_entry(f: dict) -> dict:
	entry = {"fieldname": f["fieldname"], "label": f.get("label") or f["fieldname"], "fieldtype": f.get("fieldtype", "")}
	options = f.get("options")
	if options and f.get("fieldtype") in ("Link", "Table", "Table MultiSelect", "Dynamic Link"):
		entry["options"] = options
	elif options and f.get("fieldtype") == "Select":
		entry["choices"] = [o for o in str(options).splitlines() if o.strip()]
	return entry


def _split_fields(fields_json: str | None) -> tuple[list[dict], list[dict], list[dict]]:
	"""(required, optional, tables) from a schema node's fields_json."""
	required, optional, tables = [], [], []
	for f in json.loads(fields_json or "[]"):
		fieldname, fieldtype = f.get("fieldname"), f.get("fieldtype", "")
		if not fieldname or fieldname in SKIP_FIELDS or fieldtype in LAYOUT_TYPES:
			continue
		if fieldtype in ("Table", "Table MultiSelect"):
			tables.append(f)
		elif f.get("reqd"):
			required.append(_field_entry(f))
		elif len(optional) < MAX_OPTIONAL:
			optional.append(_field_entry(f))
	return required, optional, tables


def action_tools(ctx: ToolContext) -> list[Tool]:
	def describe(doctype: str) -> dict:
		if not visible(ctx, doctype):
			raise ValueError(f"You don't have access to {doctype}.")
		run = ctx.schema.ro(f"Schema of {doctype}", DESCRIBE_DOCTYPE, {"doctype": doctype})
		if not run.rows:
			raise ValueError(f"Unknown DocType '{doctype}'.")
		row = run.rows[0]
		row["fields"] = filter_fields_json(ctx, doctype, row.get("fields"))
		return row

	def record_form(args: RecordFormArgs) -> dict:
		row = describe(args.doctype)
		required, optional, table_fields = _split_fields(row.get("fields"))
		tables = []
		for f in table_fields:
			child = f.get("options")
			if not child:
				continue
			try:
				child_required, child_optional, _ = _split_fields(describe(child).get("fields"))
			except ValueError:
				continue
			tables.append(
				{
					"fieldname": f["fieldname"],
					"label": f.get("label") or f["fieldname"],
					"child_doctype": child,
					"required": child_required,
					"optional": child_optional[:10],
				}
			)
		form = {
			"doctype": args.doctype,
			"is_submittable": bool(row.get("is_submittable")),
			"required": required,
			"optional": optional,
			"tables": tables,
			"writable": ctx.config.allow_create and args.doctype in ctx.config.write_doctypes,
		}
		out = result(ctx, [], [])
		out["form"] = form
		return out

	def propose_record(args: ProposeRecordArgs) -> dict:
		config = ctx.config
		allowed = config.allow_create if args.mode == "create" else config.allow_update
		if not allowed:
			raise ValueError(
				f"Eve is not allowed to {args.mode} records. A System Manager can turn this on in "
				"Eve Agent Settings → Eve Actions."
			)
		if args.doctype not in config.write_doctypes:
			raise ValueError(
				f"Eve may not write {args.doctype}. Allowed DocTypes: {', '.join(config.write_doctypes) or 'none'}."
			)
		if args.mode == "update" and not args.name:
			raise ValueError("An update needs the exact record name (use find_entity).")
		if any(key in RESERVED_VALUES for key in args.values):
			raise ValueError("Do not set docstatus, name or amended_from: records are always created as drafts.")
		if ctx.can_write is not None and not ctx.can_write(args.doctype, "create" if args.mode == "create" else "write"):
			raise ValueError(f"{ctx.user.name} does not have permission to {args.mode} {args.doctype} records.")

		is_submittable = False
		try:
			is_submittable = bool(describe(args.doctype).get("is_submittable"))
		except ValueError:
			pass
		out = result(ctx, [], [], "Waiting for the user to confirm on the card.")
		out["action"] = {
			"kind": args.mode,
			"doctype": args.doctype,
			"name": args.name,
			"values": args.values,
			"summary": args.summary,
			"is_submittable": is_submittable,
			"status": "pending",
		}
		return out

	return [
		Tool(
			"record_form",
			" ".join(
				[
					"The fields a DocType record needs: required fields, common optional fields, and child tables with their fields.",
					"Call before proposing a record. Ask the user for the required values (and child rows, e.g. items with item_code and qty) you cannot infer, then call propose_record.",
					"Resolve links (customer, item, warehouse...) with find_entity first so names are exact.",
				]
			),
			RecordFormArgs,
			record_form,
		),
		Tool(
			"propose_record",
			" ".join(
				[
					"Propose a new record (or an update to a draft) for the user to confirm. Nothing is written until they click Create on the card.",
					"Submittable documents are created as drafts only; Eve never submits, the user does that in the Desk.",
					"The tool output's status stays 'pending' until the user acts; a later turn shows 'created', 'cancelled' or 'error'.",
				]
			),
			ProposeRecordArgs,
			propose_record,
		),
	]
