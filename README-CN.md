<!-- mcp-name: io.github.vmware-skills/vmware-privateai -->

# VMware Private AI(Foundation with NVIDIA)—— GPU 与模型服务运维

VMware skill 家族的 **GPU / AI 基础设施** 视角 —— GPU 主机与设备清点、vGPU 消费者、实时 GPU 利用率、
vGPU / DirectPath 配置目录、vGPU 分配,以及 **Private AI Service(PAIS)** 的已服务模型与知识库,
覆盖 **vSphere 9.x / VCF 9.1** Web Services API(pyVmomi)与 PAIS REST API。MCP server + CLI。

> **声明**:社区维护的开源项目,**与 VMware, Inc.、Broadcom Inc.、NVIDIA Corporation 无任何隶属、背书或
> 赞助关系。** "VMware"、"vSphere"、"VCF" 为 Broadcom 商标;"NVIDIA"、"vGPU" 为 NVIDIA 商标。源码遵循
> MIT 许可,公开可审计:[github.com/vmware-skills/VMware-PrivateAI](https://github.com/vmware-skills/VMware-PrivateAI)。

> **状态:v0.1.0(beta)。** 家族第 15 个 skill,走独立 1.x 版本线。每条 API 路径在使用前都对照官方
> Broadcom/NVIDIA 资料核实(见 `tests/eval/spec/privateai_endpoints.py`)——不凭记忆编写端点。GET 响应
> **字段名** 与 PAIS 的确切路径为防御式,待真机 9.x 硬件验证(见下方 beta 活口)。由家族 harness 治理
> (审计 + 策略 + 教学性错误);读写授权交由 vCenter 服务账号的 RBAC 角色。

## 能力(10 工具:9 读 / 1 写)

| 类别 | 工具 | 读/写 |
|------|------|:----:|
| **GPU 清点** | 主机 列表/详情、设备列表、vGPU 消费者列表 | 4 读 |
| **GPU 利用率** | 每台 vGPU 虚机实时利用率(GPU %、显存 %、温度) | 1 读 |
| **配置目录** | vGPU 配置列表、DirectPath 配置列表 | 2 读 |
| **vGPU 分配** | 设置虚机 vGPU 配置(虚机须已关机) | 1 写 |
| **Private AI Service** | 已服务模型列表、知识库列表 | 2 读 |

读操作严格无破坏性。唯一的写(`vgpu_assign`)会预览爆炸半径、拒绝已开机虚机、**绝不自行关机**、
CLI 双重确认、统一审计到 `~/.vmware/audit.db`。

## 快速开始

```bash
uv tool install vmware-privateai
vmware-privateai version
vmware-privateai gpu host-list        # 第一条读命令 —— 列出有 GPU 的主机
```

配置位于 `~/.vmware-privateai/config.yaml`(targets + 可选 `pais:` 段);密码与 PAIS bearer token 存于
`~/.vmware-privateai/.env`(chmod 600)。详见 `skills/vmware-privateai/references/setup-guide.md`。

## 典型工作流

**1. 找空闲 GPU 并给虚机换 vGPU 配置**
```bash
vmware-privateai gpu device-list --vendor NVIDIA   # vm_count 0 = 空闲
vmware-privateai gpu consumer-list                 # 谁持有 vGPU、什么配置
vmware-privateai vgpu profile-list --host esx-07   # 该主机可分配的配置
vmware-privateai gpu vgpu-assign fin-train-01 grid_a100-4c --dry-run   # 预览
# 先用 vmware-aiops 关机,再:
vmware-privateai gpu vgpu-assign fin-train-01 grid_a100-4c            # 双重确认 + 审计
```
*失败分支*:提示 "VM is powered on" 时,先 `vmware-aiops vm_power_off 'fin-train-01'`;提示配置不被主机
提供 / 显存不足时,`vmware-privateai gpu host-get <该主机>` 查看有效配置。

**2. 全域 GPU 利用率巡检**
```bash
vmware-privateai gpu utilization --top 10
```
*失败分支*:显示 `metrics unavailable` 不是错误 —— NVIDIA 主机 GPU 驱动未暴露该虚机的计数器。更深的
per-SM / MIG-slice 遥测 vSphere 不提供(需 NVIDIA DCGM)。

**3. 查看 PAIS 正在服务什么**
```bash
vmware-privateai pais model-list
vmware-privateai pais kb-list
```
*失败分支*:HTTP 404 通常是 base-URL 不匹配(`/api/v1` 前缀随部署而异、beta 未确认),查 `pais.endpoint`;
HTTP 401/403 表示 `VMWARE_PRIVATEAI_PAIS_TOKEN` 过期或缺权限,重新取 token。

## Companion Skills
- [vmware-aiops](https://github.com/vmware-skills/VMware-AIops) —— AI 负载背后的 vCenter 虚机(开关机/快照/克隆/迁移)
- [vmware-vks](https://github.com/vmware-skills/VMware-VKS) —— GPU 使能的 Tanzu Kubernetes
- [vmware-monitor](https://github.com/vmware-skills/VMware-Monitor) —— 只读 vSphere 健康

## 安全
详见仓库根目录 `SECURITY.md`。要点:凭据仅存 `.env`(chmod 600、`b64:` 混淆);默认开启 TLS 校验;
所有 vSphere/PAIS 文本经 `vmware_policy.sanitize()` 防注入;读写授权交由 vCenter RBAC;
`vgpu_assign` 关机门控 + 双重确认 + 审计。

## License
MIT
