from eve_agent.sync.schema_sync import enqueue_schema_sync


def after_install():
	enqueue_schema_sync()


def after_migrate():
	enqueue_schema_sync()
