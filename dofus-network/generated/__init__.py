"""Generated cross-language contracts. Do not edit by hand.

`schema/*.schema.json` is canonical; `python tools/codegen_schemas.py` writes this
package. Import the validator for a payload you are about to trust:

    from generated.contracts import validate_bridge_health

    errors = validate_bridge_health(payload)
    if errors:
        raise RuntimeError("; ".join(errors))
"""
