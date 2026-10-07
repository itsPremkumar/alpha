#!/usr/bin/env python
import ast, glob, os

ROOT = "/c/Users/PREM KUMAR/Videos/alpha/backend"
# Routers known to need manual auth review (no require_auth/require_permission on handler)
MANUAL_REVIEW = ["commands.py"]  # plus others we inspect manually

for f in sorted(glob.glob(ROOT + "/app/gateway/routers/*.py")):
    tree = ast.parse(open(f, encoding="utf-8").read())
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decos = []
            for d in n.decorator_list:
                if isinstance(d, ast.Call):
                    fn = d.func
                    if isinstance(fn, ast.Attribute) and fn.attr in ("get","post","put","patch","delete","head","options","trace"):
                        decos.append(fn.attr)
            if not decos:
                continue
            # check for require_auth / require_permission decorators
            has_auth_deco = any(
                (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id in ("require_auth","require_permission")) or
                (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in ("require_auth","require_permission"))
                for d in n.decorator_list
            )
            if not has_auth_deco and n.name not in ("health_check","readiness_check","moa_status","local_endpoint_health","free_llm_catalog","get_providers","provider_credentials_storage_status","probe_free_models_endpoint","list_discovered_models","refresh_discovered_models","list_models","get_model","list_providers","free_llm_catalog"):
                # show handler only
                pass
            print(f"{os.path.basename(f):28s} {n.name:42s} {decos}")
