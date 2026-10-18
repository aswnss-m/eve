"""Build one access profile's data graph through the regular data sync pipeline."""

from eve_agent.access.reader import ProfileReader
from eve_agent.access.scope import AccessScope, filter_plan
from eve_agent.sync.data_sync import base_data_graph, build_data_graph, load_plan


def profile_graph_names(settings, key: str) -> tuple[str, str]:
	"""The two blue/green slots of profile `key`'s graph."""
	name = f"{base_data_graph(settings)}_p{key}"
	return name, f"{name}_next"


def build_profile_graph(settings, scope: AccessScope, graph_name: str) -> str:
	"""Always passes the filtered plan: that is the only thing narrowing the reader's fields."""
	plan = filter_plan(load_plan(settings), scope)
	return build_data_graph(settings, graph_name, plan=plan, reader=ProfileReader(scope))
