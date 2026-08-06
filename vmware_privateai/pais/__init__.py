"""Private AI Service (PAIS) REST client — a per-namespace, OpenAI-style HTTP endpoint.

This is a SEPARATE connection flavor from the pyVmomi vCenter connection: PAIS speaks
HTTP with OIDC/OAuth2 bearer auth, not the vSphere Web Services API. Model-serving and
knowledge-base reads go through :class:`vmware_privateai.pais.client.PaisClient`.
"""
