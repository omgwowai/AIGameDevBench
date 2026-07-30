# testcases_quarantine/

因未通过 `scripts/audit_solvability.py` 可解性门禁而从 `testcases_filtered/` 隔离的 case。

| case | 原因 | 说明 |
|---|---|---|
| `survey-history_2023-06-09_73dc20c_000` | BINARY_PATCH | 真实缺陷为缺失字体/PNG 二进制资源（A3 L0 崩溃），`good.diff` 含 GIT binary patch。agent 无法凭空重建二进制资产，golden 无法在"仅编辑文本文件"约束下重放。若要复活：将任务重写为"移除失效资源引用使项目可加载"，并重制纯文本 golden 与验证器。 |

入库规则：任何进入 `testcases_filtered/` 的 case 必须先通过
`python scripts/audit_solvability.py testcases_filtered`（CI 强制）。
