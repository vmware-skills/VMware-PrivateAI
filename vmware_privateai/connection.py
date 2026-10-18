"""Connection management for vCenter and ESXi hosts (GPU inventory source).

Handles multi-target connections via pyVmomi with session reuse. GPU inventory,
vGPU profiles, and utilization are read through this vSphere connection.
"""

from __future__ import annotations

import atexit
import socket
import ssl
from collections.abc import Callable
from typing import TYPE_CHECKING

from pyVmomi import vim

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

from vmware_policy import sanitize
from vmware_policy.access import login_has_no_session, no_role_message
from vmware_policy.pool import SessionPool

from vmware_privateai.config import CONFIG_FILE, AppConfig, ConfigError, TargetConfig, load_config

# atexit cleanups for live connections, keyed by id(si) so a connection dropped
# before interpreter exit can take its handler with it.
_SI_ATEXIT: dict[int, Callable[[], None]] = {}


def _release_si(si: ServiceInstance) -> None:
    """Unregister the atexit cleanup registered for ``si``.

    Every connect() registers a cleanup that closes over si, and atexit holds
    that closure -- and therefore si -- until the process exits. A long-running
    MCP server that reconnects after each session expiry (踩坑 #40) accumulates
    one dead ServiceInstance and one handler per reconnect, and at exit runs a
    Disconnect against every session it ever opened.

    Measured before this existed: 50 evict-and-reconnect cycles left 50 handlers
    registered and all 50 evicted ServiceInstance objects still reachable, while
    the id(si) side stores stayed correctly at one entry -- the side-store
    discipline was never the leak, the registration was.

    Boundary: this covers the paths that drop a *connection* -- the eviction
    inside connect() and disconnect(). A caller that drops the whole
    ConnectionManager while it still holds connections leaks them exactly as
    before, since both atexit and this table still reach them. Unchanged rather
    than fixed: the reconnect loop is the shape that grows without bound.
    """
    fn = _SI_ATEXIT.pop(id(si), None)
    if fn is not None:
        atexit.unregister(fn)


class NoVsphereRoleError(ConfigError):
    """The login was accepted but the account has no vSphere role (AIops#47).

    A ``ConfigError`` so the CLI and ``_safe_error`` already show its text; a
    class of its own so callers can tell it from a transport failure. The
    message is authored: the target name from config.yaml, nothing from the server.
    """


def login_refused_message(target: str, cli: str, *, privilege: str | None = None, esxi: bool = False) -> str:
    """The teaching error when the login itself is refused with NoPermission.

    vCenter 8.0.3 refuses the login, with NoPermission for System.View on the
    root folder, for an SSO account holding no vSphere permission anywhere (lab,
    2026-10-18); an account with a permission lower down still logs in. Only
    that case gets the "no vSphere role" remedy. An ESXi host (lockdown mode,
    host permissions) or another privilege gets a neutral text naming the
    privilege vCenter returned (sanitized, capped at 48). About 300 characters.
    """
    if not esxi and privilege == "System.View":
        return (
            f"'{target}' refused the login: the password is right but the account has "
            "no vSphere role (no System.View on the root). Assign Read-Only or higher "
            f"(vCenter: Global Permissions), then run '{cli} doctor'. A new password "
            "will not help."
        )
    # vCenter's own string: sanitized and bounded so the text stays short.
    need = f" ({sanitize(privilege, 48)})" if privilege else ""
    return (
        f"'{target}' refused the login with NoPermission{need}: the password is right "
        "but this account may not log in there. On ESXi check the host's Permissions "
        f"and lockdown mode; on vCenter assign a role. Then run '{cli} doctor'."
    )


NOT_AUTHENTICATED_HINT = (
    "vCenter answered 'the session is not authenticated'. Either the session "
    "expired (run the command again; a new session is opened) or the account "
    "has no vSphere role (assign Read-Only or higher on the vCenter root folder, "
    "then run 'vmware-privateai doctor'). Changing the password will not help."
)


def _session_alive(si: ServiceInstance) -> bool:
    """Probe a cached session; any failure, or a None currentSession, is dead.

    An expired token can surface as ``currentSession is None`` instead of
    raising (踩坑 #40), so both shapes count as dead.
    """
    try:
        return si.content.sessionManager.currentSession is not None
    except Exception:
        return False


def _close_si(si: ServiceInstance) -> None:
    """Everything dropping a session must do; never raises.

    Unregisters the atexit handler that pins ``si`` until exit, then logs out,
    which may fail on a session that is already dead.
    """
    from pyVim.connect import Disconnect

    _release_si(si)
    try:
        Disconnect(si)
    except Exception:
        pass


class ConnectionManager:
    """Manages connections to multiple vCenter/ESXi targets.

    Sessions live in a :class:`vmware_policy.pool.SessionPool` keyed by target
    name (HLD §16 D-13(2)): inside an MCP call each call holds its own session
    for the whole call, so two calls never interleave SOAP on one connection;
    the CLI and stdio (no call scope) get one cached session per target.
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._pool: SessionPool[ServiceInstance] = SessionPool(
            "vmware-privateai",
            # Looked up on self at call time so a test can patch
            # _create_connection on one manager.
            create=lambda name: self._create_connection(self._config.get_target(name)),
            is_alive=_session_alive,
            close=_close_si,
        )

    @classmethod
    def from_config(cls, config: AppConfig | None = None) -> ConnectionManager:
        cfg = config or load_config()
        return cls(cfg)

    def resolve_target_name(self, target_name: str | None = None) -> str:
        """The configured name ``connect(target_name)`` would use (the default if None)."""
        target = self._config.get_target(target_name) if target_name else self._config.default_target
        return target.name

    def connect(self, target_name: str | None = None) -> ServiceInstance:
        """Connect to a target by name, or the default target."""
        return self._pool.get(self.resolve_target_name(target_name))

    def disconnect(self, target_name: str) -> None:
        """Disconnect from a specific target (busy sessions close when their call ends)."""
        self._pool.discard(target_name)

    def disconnect_all(self) -> None:
        """Disconnect from all targets."""
        self._pool.close_all()

    def list_targets(self) -> list[str]:
        """List all configured target names."""
        return [t.name for t in self._config.targets]

    def list_connected(self) -> list[str]:
        """List currently connected target names."""
        return self._pool.keys()

    @staticmethod
    def _create_connection(target: TargetConfig) -> ServiceInstance:
        """Create a new pyVmomi connection."""
        from pyVim.connect import Disconnect, SmartConnect

        context = None
        if not target.verify_ssl:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        # Resolve credentials BEFORE the try block. Both are properties, and the
        # missing-password one raises ConfigError — an OSError subclass the
        # handlers below would otherwise relabel as a TLS/DNS failure. Read
        # adjacently so a sidecar rotating both halves cannot split them.
        user, pwd = target.username, target.password

        try:
            si = SmartConnect(
                host=target.host,
                user=user,
                pwd=pwd,
                port=target.port,
                sslContext=context,
                disableSslCertValidation=not target.verify_ssl,
            )
        except ssl.SSLError as exc:
            raise ConfigError(
                f"TLS verification failed for target '{target.name}' — set "
                f"verify_ssl: false on that target in {CONFIG_FILE} if it uses a "
                f"self-signed certificate, or install its CA on this host."
            ) from exc
        except socket.gaierror as exc:
            raise ConfigError(
                f"Could not resolve the host configured for target '{target.name}' "
                f"— check that target's 'host' value in {CONFIG_FILE} for a typo "
                f"or a DNS suffix this machine cannot resolve."
            ) from exc
        except OSError as exc:
            raise ConnectionError(
                f"Could not reach target '{target.name}' — check that the "
                f"vCenter/ESXi host is up and that its 'host' and 'port' in "
                f"{CONFIG_FILE} are reachable from this machine."
            ) from exc
        except vim.fault.NoPermission as exc:
            # A wrong password is InvalidLogin, not this; it passes through.
            raise NoVsphereRoleError(
                login_refused_message(
                    target.name,
                    "vmware-privateai",
                    privilege=getattr(exc, "privilegeId", None),
                    esxi=target.type == "esxi",
                )
            ) from exc

        def _cleanup(_si: ServiceInstance = si) -> None:
            try:
                Disconnect(_si)
            except Exception:
                pass

        _SI_ATEXIT[id(si)] = _cleanup
        atexit.register(_cleanup)

        # An account with no vSphere role logs in fine and then reads nothing:
        # vCenter shows no session for it, a root-folder view is refused, and
        # every call fails with NotAuthenticated (AIops#47). Just after login an expired session is
        # not the explanation, so refuse here, where doctor sees it too, and
        # close the session the way the pool would so nothing caches it.
        if login_has_no_session(si):
            _close_si(si)
            raise NoVsphereRoleError(no_role_message(target.name, "vmware-privateai"))
        return si


def get_content(si: ServiceInstance) -> vim.ServiceInstanceContent:
    """Shortcut to get ServiceContent from a ServiceInstance."""
    return si.RetrieveContent()
