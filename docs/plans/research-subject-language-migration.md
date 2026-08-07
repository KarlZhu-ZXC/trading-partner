# Research Subject 领域语言迁移

## 决策

产品界面统一使用“标的”或“研究标的”，英文领域代码统一使用
`ResearchSubject`。`Equity` 只表示真实的股票类 Instrument，不用于主题、宏观、
催化剂、组合问题、商品或其他跨资产研究对象。

原 `Investment Case` 表示的是一份可长期积累证据、Thesis、Trade Plan 与监控
关系的研究档案，而不是一笔交易方案。迁移后的关系为：

```text
Research Subject（标的/研究范围）
├── Thesis（当前可证伪判断）
├── Trade Plan（执行品种与行动条件）
├── Evidence / Report / Journal / Decision
└── Monitor（可观察条件，可引用不同的事实标的）
```

## Canonical contract

- 领域模型、应用服务与 Console 代码：`ResearchSubject`、`subject_id`、
  `subject_type`、`linked_subject_ids`。
- 用户界面和活跃文档：标的、研究标的、研究档案；不再展示 Case。
- Console canonical anchor：`#subject-<id>`；继续解析历史 `#case-<id>` 书签。
- compact MCP 的用户可见 title/description 使用 Research Subject / 标的。
- 公共工具数量仍严格保持 28。

## Compatibility boundary

以下名称是历史数据与客户端兼容 ABI，不代表新的领域语言：

- opaque ID 继续使用 `case_<uuid7>`；迁移不批量改写已有 ID，也不产生混合前缀。
- SQLite 物理表、列、外键、索引和约束继续使用既有
  `investment_cases` / `case_id` 等名称；ORM 将其显式映射为新的 Python 属性。
- 已发布 MCP 机器工具名 `investment_case_read` / `investment_case_manage`，以及
  wire 字段 `case_id` / `case_type` / `linked_case_ids` 暂时保留在单一接口适配层。
  这样既不增加工具数量，也不让双字段耗尽 schema 预算。
- 历史状态值、错误码、audit event、append-only JSON、payload hash 与幂等记录
  原样可读；decoder 可归一为 Subject 语言，但不得原地重写历史记录。
- 已提交的 Alembic migration 是历史事实，不回写。归档设计文档保留旧 API
  事实，并在顶部说明术语已经迁移。

## Non-goals

- 不把普通 `asset`、`instrument` 或证券类型机械改成 Subject/Equity。
- 不修改普通英语中的 test case、use case、case-insensitive、switch case。
- 不自动改变 Subject、Thesis、Trade Plan 或 Monitor 的生命周期状态。
- 不借术语迁移增加新的 MCP 工具或交易执行能力。

## Acceptance

1. 新代码的领域 API 使用 `ResearchSubject` / `subject_*`。
2. Console、Skill、MCP 描述和活跃文档不再把研究档案称作 Case。
3. 旧数据库能直接读取 Subject、Thesis、Trade Plan、Monitor、Watchlist 与时间线。
4. 历史 `case_` ID、旧 MCP wire 和旧 URL anchor 仍可用。
5. MCP surface 仍为 28 个工具，schema version 升级且 schema 预算通过。
6. Ruff、mypy、pytest、Console lint/build/test 和 migration smoke 全部通过。
