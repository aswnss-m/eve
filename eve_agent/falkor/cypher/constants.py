"""Labels, relationship types and property names used in the schema graph."""

DOCTYPE_LABEL = "DocType"

LINKS_TO = "LINKS_TO"  # Link field: DocType -> target DocType
HAS_CHILD = "HAS_CHILD"  # Table field: parent DocType -> child DocType

DOCTYPE_PROPERTIES = (
	"module",
	"is_submittable",
	"is_single",
	"is_child",
	"is_tree",
	"title_field",
	"field_count",
	"field_names",
	"field_types",
	"link_targets",
	"fields_json",
	"modified",
)

SCHEMA_BATCH_SIZE = 500
