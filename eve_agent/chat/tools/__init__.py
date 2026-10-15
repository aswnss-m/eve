from eve_agent.chat.common import Tool, ToolContext
from eve_agent.chat.memory import memory_tools
from eve_agent.chat.tools.brain import brain_tools
from eve_agent.chat.tools.entities import entity_tools
from eve_agent.chat.tools.sales import sales_tools
from eve_agent.chat.tools.utility import utility_tools


def build_tools(ctx: ToolContext) -> list[Tool]:
	return [
		*entity_tools(ctx),
		*sales_tools(ctx),
		*brain_tools(ctx),
		*utility_tools(ctx),
		*memory_tools(ctx),
	]
