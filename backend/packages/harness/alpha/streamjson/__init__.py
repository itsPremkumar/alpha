"""Machine-readable run output and the generated wire-contract types.

``streamjson``   a stream-JSON emitter whose every field is accounted for.
``wire_contracts``  Pydantic models that are the authoritative server/client
                 contract, plus a TypeScript generator so frontend drift becomes
                 a type error instead of a runtime surprise.
"""
