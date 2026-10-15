"""vSphere privileges each MCP tool needs (HLD §17 D-17), for ``get_access_info``.

Derived from the pyVmomi calls each tool's ops make, using pyVmomi's own type
metadata (``_GetMethodInfo(name).privId`` / property ``privId``):

* GPU inventory walks a ContainerView (``ViewManager.CreateContainerView`` ->
  System.View) with the PropertyCollector and reads host/VM ``config`` and
  ``hardware`` properties (System.Read);
* ``PerformanceManager.QueryPerf`` (utilization, monitoring summary),
  ``EnvironmentBrowser.QueryConfigTarget`` (vGPU profile catalog, readiness,
  validation) and ``DirectPathProfileManager.ListDirectPathProfiles`` are
  System.View;
* ``VirtualMachine.ReconfigVM_Task`` (``assign_vgpu``) carries no privId in the
  metadata. vSphere checks the device change itself: adding a PCI/vGPU device
  needs VirtualMachine.Config.AddRemoveDevice, a reviewed extra in the family
  gate (verify against a live vCenter's privilegeList).

The PAIS tools call the Private AI Service REST API with a bearer token from the
Identity Provider, not vSphere, so they need no vSphere privilege; whether the
token's scope allows them is not something vCenter can answer. The sizing and
bundle tools read only their arguments or a local file.

The family gate (scripts/lib/access_requirements_cover_tools.py) checks that the
keys are exactly the registered tools and that every id is one vSphere defines.
"""

from __future__ import annotations

_READ = ["System.View", "System.Read"]

REQUIREMENTS: dict[str, list[str]] = {
    "advise_pais_sizing": [],  # arithmetic on its arguments
    "verify_pais_bundle": [],  # parses a local pais.yml
    "list_gpu_hosts": list(_READ),
    "get_gpu_host": list(_READ),
    "list_gpu_devices": list(_READ),
    "get_gpu_utilization": list(_READ),
    "list_gpu_consumers": list(_READ),
    "check_gpu_host_readiness": list(_READ),
    "assign_vgpu": [*_READ, "VirtualMachine.Config.AddRemoveDevice"],
    "list_pais_models": [],  # PAIS REST, bearer token; see above
    "list_pais_knowledge_bases": [],
    "get_pais_model_catalog": [],
    "list_pais_data_sources": [],
    "get_pais_monitoring_summary": list(_READ),  # vSphere perf counters, not PAIS
    "list_vgpu_profiles": list(_READ),
    "list_directpath_profiles": list(_READ),
    "validate_vgpu_profile": list(_READ),
    # AuthorizationManager.HasPrivilegeOnEntities / RetrieveAllPermissions /
    # roleList are all System.View.
    "get_access_info": ["System.View"],
}
