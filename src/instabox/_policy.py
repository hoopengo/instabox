"""SDK-level tool policy: restrict which operations a client may perform.

Two layers of access control exist for InstaBox:

1. **API key scopes** (server-side, authoritative): keys carry scopes such as
   ``machines:read``, ``machines:write``, ``machines:delete``, ``images:read``
   and ``workspace:admin``. A request outside a key's scopes fails with
   ``403 missing_scope``. Create scoped keys in the dashboard or with
   ``instabox keys create --scope ...``.

2. **SDK tool policy** (client-side, this module): an allow/deny list of
   operation names such as ``machines.create`` or ``machines.exec``. Use it
   when one code path (e.g. an AI agent) shares a broader key but must not
   touch certain operations — the SDK raises
   :class:`instabox.exceptions.PermissionDeniedError` before any network
   request is made.

Operation names follow ``<resource>.<method>``: ``machines.*``,
``operations.*``, ``snapshots.*``, ``ports.*``, ``account.*``, plus
``machines.ssh_tokens.*``, ``machines.secrets.*`` and ``orgs.secrets.*``
aliases for the nested resources.
"""

from __future__ import annotations

import builtins
import inspect
from collections.abc import Callable
from typing import Any

from .exceptions import PermissionDeniedError

# Nested namespaces that are checked as their own prefix, in addition to the
# resource class name under which they live.
_ALIASES = {
    "SshTokens": "machines.ssh_tokens",
    "MachineSecrets": "machines.secrets",
    "OrgSecrets": "orgs.secrets",
}

_POLICY_ATTR = "_instabox_policy_prefix"


def operation_name(resource_class_name: str, method_name: str) -> str:
    """Return the dotted operation name for a resource method."""
    base = resource_class_name.lstrip("_")
    if base.startswith("Async"):
        base = base[len("Async") :]
    prefix = _ALIASES.get(resource_class_name, base.lower())
    return f"{prefix}.{method_name}"


def check_allowed(policy: ToolPolicy | None, name: str) -> None:
    """Raise :class:`PermissionDeniedError` if the policy disallows ``name``."""
    if policy is not None and not policy.allows(name):
        raise PermissionDeniedError(
            status=403,
            code="sdk_not_allowed",
            message=(
                f"operation '{name}' is blocked by this client's tool policy "
                f"(allowed: {builtins.sorted(policy.allow) or '<none>'})"
            ),
        )


class ToolPolicy:
    """Allow/deny list of operation names (``resource.method`` strings).

    Args:
        allow: If given, only these operations (or prefixes ending in ``.*``)
            are permitted. ``None`` means everything is allowed unless denied.
        deny: Operations (or prefixes) always forbidden; wins over ``allow``.

    Example::

        policy = ToolPolicy(
            allow=["machines.exec", "machines.read_file", "operations.*"],
            deny=["machines.delete"],
        )
        client = Client(api_key=key, policy=policy)
    """

    __slots__ = ("allow", "deny")

    def __init__(
        self,
        allow: builtins.set[str] | builtins.list[str] | None = None,
        deny: builtins.set[str] | builtins.list[str] | None = None,
    ) -> None:
        self.allow: frozenset[str] = frozenset(allow or ())
        self.deny: frozenset[str] = frozenset(deny or ())

    def _match(self, names: frozenset[str], name: str) -> bool:
        if name in names:
            return True
        prefix = name + "."
        return any(n.endswith(".*") and prefix.startswith(n[:-1]) for n in names)

    def allows(self, name: str) -> bool:
        if self._match(self.deny, name):
            return False
        if not self.allow:  # nothing specified -> allow all (minus deny)
            return True
        return self._match(self.allow, name)


def wrap_resource_methods(resource: Any) -> None:
    """Wrap each public method of ``resource`` with a policy check.

    The operation name is derived from the resource class name and the method
    name; the check runs before any request is made.
    """
    cls = resource.__class__
    if getattr(cls, "_policy_wrapped", False):
        return
    cls._policy_wrapped = True
    prefix = operation_name(cls.__name__, "")
    for attr, value in vars(cls).items():
        if attr.startswith("_") or not inspect.isfunction(value):
            continue
        name = f"{prefix}{attr}"

        def make_wrapper(func: Callable[..., Any], op_name: str) -> Callable[..., Any]:
            def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
                check_allowed(getattr(self, "_policy", None), op_name)
                return func(self, *args, **kwargs)

            if inspect.iscoroutinefunction(func):

                async def async_wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
                    check_allowed(getattr(self, "_policy", None), op_name)
                    return await func(self, *args, **kwargs)

                async_wrapper.__name__ = func.__name__
                async_wrapper.__doc__ = func.__doc__
                return async_wrapper

            wrapper.__name__ = func.__name__
            wrapper.__doc__ = func.__doc__
            return wrapper

        setattr(cls, attr, make_wrapper(value, name))
