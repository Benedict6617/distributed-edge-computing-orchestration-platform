"""
A minimal regex-path router for `http.server.BaseHTTPRequestHandler`
subclasses. This is not a general-purpose web framework -- it is ~60 lines
that give the control plane clean route definitions without a Flask/FastAPI
dependency, which keeps the whole project runnable with nothing but
`python3`.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, List, Tuple

RouteHandler = Callable[..., "object"]


class Route:
    def __init__(self, method: str, pattern: str, handler: RouteHandler):
        self.method = method.upper()
        # Convert "/api/v1/nodes/{node_id}" into a compiled regex with named groups.
        regex_pattern = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern)
        self.regex = re.compile(f"^{regex_pattern}$")
        self.handler = handler


class Router:
    def __init__(self):
        self._routes: List[Route] = []

    def add(self, method: str, pattern: str, handler: RouteHandler) -> None:
        self._routes.append(Route(method, pattern, handler))

    def get(self, pattern: str):
        def deco(fn):
            self.add("GET", pattern, fn)
            return fn
        return deco

    def post(self, pattern: str):
        def deco(fn):
            self.add("POST", pattern, fn)
            return fn
        return deco

    def delete(self, pattern: str):
        def deco(fn):
            self.add("DELETE", pattern, fn)
            return fn
        return deco

    def resolve(self, method: str, path: str):
        for route in self._routes:
            if route.method != method.upper():
                continue
            match = route.regex.match(path)
            if match:
                return route.handler, match.groupdict()
        return None, {}
