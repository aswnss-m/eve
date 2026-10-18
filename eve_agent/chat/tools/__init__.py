from eve_agent.chat.common import Tool, ToolContext, visible
from eve_agent.chat.memory import memory_tools
from eve_agent.chat.tools.actions import action_tools
from eve_agent.chat.tools.brain import brain_tools
from eve_agent.chat.tools.charts import chart_tools
from eve_agent.chat.tools.entities import entity_tools
from eve_agent.chat.tools.sales import sales_tools
from eve_agent.chat.tools.utility import utility_tools

_CUSTOMER_SALES = (frozenset({"Customer"}), frozenset({"Sales Invoice", "Sales Order"}))
_ITEM_SALES = (frozenset({"Item"}), frozenset({"Sales Invoice", "Sales Order"}))

# A tool is offered only when every group has at least one visible DocType. Others are always offered.
TOOL_NEEDS: dict[str, tuple[frozenset[str], ...]] = {
	"top_customers": _CUSTOMER_SALES,
	"dormant_customers": _CUSTOMER_SALES,
	"customer_profile": (frozenset({"Customer"}),),
	"top_selling_items": _ITEM_SALES,
	"also_bought": _ITEM_SALES,
	"item_profile": (frozenset({"Item"}),),
	"top_suppliers": (frozenset({"Supplier"}), frozenset({"Purchase Invoice", "Purchase Order"})),
	"supplier_risk": (
		frozenset({"Item"}),
		frozenset({"Supplier"}),
		frozenset({"Purchase Invoice", "Purchase Order"}),
	),
}


def _offered(ctx: ToolContext, tool: Tool) -> bool:
	return all(any(visible(ctx, d) for d in group) for group in TOOL_NEEDS.get(tool.name, ()))


def _tracked(ctx: ToolContext, tool: Tool) -> Tool:
	"""Tell the context which tool is running, for the hidden-source report."""
	run = tool.run

	def run_tool(args):
		ctx.current_tool = tool.name
		return run(args)

	tool.run = run_tool
	return tool


def build_tools(ctx: ToolContext) -> list[Tool]:
	tools = [
		*entity_tools(ctx),
		*sales_tools(ctx),
		*brain_tools(ctx),
		*utility_tools(ctx),
		*action_tools(ctx),
		*memory_tools(ctx),
		*chart_tools(ctx),
	]
	return [_tracked(ctx, t) for t in tools if _offered(ctx, t)]
