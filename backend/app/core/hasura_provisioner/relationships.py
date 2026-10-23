from dataclasses import dataclass

from .client import source_in, table_in
from .errors import HasuraUnavailable, InvalidMetadata, MetadataConflict
from .planning import relationship_operation
from .schemas import TableRef


@dataclass(frozen=True)
class RelationshipMetadata:
    source: str
    table: TableRef
    target_source: str
    target_table: TableRef
    name: str
    relationship_type: str
    field_mapping: dict[str, str]

    @property
    def remote(self):
        return self.source != self.target_source

    @property
    def collection(self):
        return "remote_relationships" if self.remote else self.relationship_type + "_relationships"

    def definition(self):
        if self.remote:
            return {
                "definition": {
                    "to_source": {
                        "relationship_type": self.relationship_type,
                        "source": self.target_source,
                        "table": self.target_table.payload(),
                        "field_mapping": self.field_mapping,
                    }
                }
            }
        return {
            "using": {
                "manual_configuration": {
                    "remote_table": self.target_table.payload(),
                    "column_mapping": self.field_mapping,
                    "insertion_order": None,
                }
            }
        }

    def args(self):
        return relationship_operation(
            self.source,
            self.table,
            self.name,
            self.relationship_type,
            self.target_source,
            self.target_table,
            self.field_mapping,
            "remote" if self.remote else "manual",
        )["args"]


def relation_state(snapshot, relation):
    source = source_in(snapshot, relation.source)
    table = table_in(source, relation.table) if source else None
    target = source_in(snapshot, relation.target_source)
    if table is None or target is None or table_in(target, relation.target_table) is None:
        raise InvalidMetadata()
    matches = []
    for collection in ("object_relationships", "array_relationships", "remote_relationships"):
        for entry in table.get(collection, []):
            if entry.get("name") in {relation.name, relation.name + "_aggregate"}:
                matches.append((collection, entry))
    if len(matches) > 1:
        raise MetadataConflict()
    if not matches:
        if any(entry.get("name") == relation.name for entry in table.get("computed_fields", [])):
            raise MetadataConflict()
        return False
    collection, entry = matches[0]
    actual = {key: value for key, value in entry.items() if key != "comment"}
    if not relation.remote:
        actual.get("using", {}).get("manual_configuration", {}).setdefault("insertion_order", None)
    if collection != relation.collection or actual != {
        "name": relation.name,
        **relation.definition(),
    }:
        raise MetadataConflict()
    return True


class HasuraRelationships:
    def __init__(self, client):
        self.client = client

    async def require(self, relation, snapshot=None):
        snapshot = snapshot or await self.client.export_metadata()
        if not relation_state(snapshot, relation):
            raise InvalidMetadata()

    async def write(self, operation, args, snapshot, relation, remove):
        try:
            await self.client._write(operation, args, snapshot)
        except MetadataConflict:
            current = await self.client.export_metadata()
            if relation_state(current, relation) == remove:
                raise HasuraUnavailable() from None

    async def apply(self, relation, *, remove=False):
        snapshot = await self.client.export_metadata()
        exists = relation_state(snapshot, relation)
        if remove and exists:
            operation = (
                "pg_delete_remote_relationship" if relation.remote else "pg_drop_relationship"
            )
            args = {
                "source": relation.source,
                "table": relation.table.payload(),
                "name": relation.name,
            }
            if not relation.remote:
                args["relationship"] = args.pop("name")
                args["cascade"] = False
            await self.write(operation, args, snapshot, relation, remove)
        elif not remove and not exists:
            operation = (
                "pg_create_remote_relationship"
                if relation.remote
                else ("pg_create_" + relation.relationship_type + "_relationship")
            )
            await self.write(operation, relation.args(), snapshot, relation, remove)
        verified = relation_state(await self.client.export_metadata(), relation)
        if verified == remove:
            raise InvalidMetadata()
        await self.client.require_consistent()
