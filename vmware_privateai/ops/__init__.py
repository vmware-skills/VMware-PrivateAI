"""Business-logic (ops) package for vmware-privateai.

GPU inventory / vGPU profiles / consumers / utilization read through pyVmomi;
vGPU assignment writes through ReconfigVM_Task; model serving reads through the
Private AI Service (PAIS) REST API. Every runtime endpoint/object path is
asserted against tests/eval/spec/privateai_endpoints.py (踩坑 #36: no phantom
endpoints — code may only touch paths verified against official docs).
"""
