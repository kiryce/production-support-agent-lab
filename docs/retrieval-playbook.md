# RAG 与检索优化手册

客服 Agent 的 RAG 不是“向量检索 top-k 塞进 prompt”。生产里更重要的是可诊断。

## RetrievalTrace 示例

```json
{
  "query": "退换货政策 质量问题 30 天",
  "rewritten_queries": [
    "退换货政策 质量问题 30 天",
    "退换货政策 质量问题 30 天 退换货 质量问题 30 天"
  ],
  "selected_sources": ["kb://policies/return_policy_v3"],
  "candidates_by_stage": {
    "hybrid": 3,
    "reranked": 3
  },
  "selected_context": [
    {
      "document_id": "return_policy_v3",
      "title": "退换货政策 v3",
      "score": 4.2
    }
  ]
}
```

如果 eval 报 `missing citation doc`，第一步就是看这个 trace。

## Retrieval challenge

端到端 eval 会同时受 intent、routing、tool、answer composition 影响。定位召回问题时，先跑 raw retrieval challenge：

```bash
python scripts/run_retrieval_eval.py
```

默认读取 `examples/evals/retrieval_challenge.json`。每个 case 会检查：

- 必须召回哪些 `document_id`。
- top-1 是否是预期文档。
- query rewrite 是否包含关键扩展词。
- candidate 数量是否低于最低要求。

这组 challenge 的作用不是证明 RAG 已经生产完美，而是保护几个最容易被改坏的基础能力：中文分词、query rewrite、政策文档召回和 trace 可诊断性。

## 当前实现

`KnowledgeIndex.search` 做了三件事：

1. Query rewrite：根据退款、物流、发票、耳机等主题扩展 query。
2. Hybrid-ish scoring：关键词/CJK token + phrase bonus。
3. RetrievalTrace：记录 query、rewritten queries、候选数量、选中来源。

这不是最终生产检索，但它保留了生产系统该有的形状。

生产模式下的 `HTTPKnowledgeIndex.search` 会把 `RetrievalContext` 透传给知识库服务：`X-Tenant-Id`、`X-Actor-User-Id`、`X-Actor-Roles`、`X-Actor-Scopes`、`X-Request-Id`、`X-Trace-Id` 和 `X-Parent-Trace-Id`。当 parent trace 是合法 W3C trace id 时，还会发送标准 `traceparent`，让知识库服务可以接入同一条 APM 链路。这让知识库可以按租户和 actor 做 ACL 过滤，并把召回日志关联回 Agent run 或 `kbdiag_*` 诊断请求。缺这些上下文时，知识库只能做“全局检索”，很容易在多租户或权限敏感场景里越界。

## 中文召回不足的真实例子

最初 tokenizer 把“耳机单边无声”当成一个长 token，知识库里是“单边无声”“蓝牙”“故障排查”，导致没有候选。

修复方式不是换模型，而是先修 tokenizer：

- 对 CJK 文本拆单字。
- 加 bigram。
- 保留英文/数字 token。
- 再看 query rewrite 和 chunk。

对应代码：`src/support_agent_lab/memory/store.py`

## 排查清单

| 症状 | 可能原因 | 修复 |
| --- | --- | --- |
| 无候选 | tokenizer、索引缺失、过滤过严 | 检查 `candidates_by_stage` |
| 候选有但答案错 | rerank 弱、上下文装配差 | 加 answerability rerank |
| 引用不支持答案 | 生成阶段没有绑定 citation | 强制 unsupported claim 检查 |
| 引用过期政策 | metadata 未参与排序 | 加 effective/version filter |
| 多语言差 | query rewrite 单语言 | 加双语 rewrite 或跨语言 embedding |

## 如何把线上失败加入 challenge

1. 从 monitor 或客服反馈里找到失败 query。
2. 打开对应 `trace.retrieval`，确认是没有候选、top-1 错、还是 citation 没被回答使用。
3. 把 query 加入 `examples/evals/retrieval_challenge.json`。
4. 先让 case 失败，确认它能复现问题。
5. 再改 tokenizer、rewrite、chunk、metadata filter 或 rerank。
6. 跑 `python scripts/run_retrieval_eval.py` 和端到端 eval，确认召回修复没有破坏最终回答。

最小模板：

```json
{
  "case_id": "retrieval_invoice_title_cn_001",
  "query": "发票抬头错了怎么改",
  "required_doc_ids": ["invoice_policy_v1"],
  "required_top_doc_id": "invoice_policy_v1",
  "required_rewrite_terms": ["发票", "税号", "抬头"],
  "min_candidates": 1
}
```

排查顺序：

1. `selected_doc_ids` 没有目标文档：先看 tokenizer、query rewrite、索引内容。
2. 目标文档出现但不是 top-1：看 rerank、metadata filter、chunk 粒度。
3. retrieval 过了但端到端 citation 失败：看回答阶段是否丢了 citation，或 route 选错导致 retrieval query 不对。

## 生产建议

- Postgres 存文档、chunk、metadata、版本。
- pgvector 做 dense retrieval。
- OpenSearch/Elasticsearch 做 BM25。
- reranker 统一输入 query + chunk + metadata。
- 所有回答带 citation。
- 用户点“没解决”后，把 query、候选、答案放入 hard query set。

## SQLite Knowledge Index

For a real local or staging knowledge base, set `APP_KNOWLEDGE_BACKEND=sqlite`
and ingest Markdown/text files before running the agent:

```bash
python scripts/knowledge_index_ops.py --database-url sqlite:///./data/knowledge/support-agent-knowledge.db --tenant-id demo_tenant --json ingest --source ./examples/knowledge --source-label policies --replace
python scripts/knowledge_index_ops.py --database-url sqlite:///./data/knowledge/support-agent-knowledge.db --tenant-id demo_tenant --json search "refund damaged headphones"
python scripts/knowledge_index_ops.py --database-url sqlite:///./data/knowledge/support-agent-knowledge.db --tenant-id demo_tenant --json stats
```

The SQLite backend writes durable `knowledge_documents`, `knowledge_chunks`, and
`knowledge_ingest_batches` tables. The console Knowledge workbench shows only
provider/status/counts/timestamps and snippets, not raw file paths, metadata, or
full document bodies. This keeps the learner path real while preserving the same
`RetrievalTrace` shape used by `HTTPKnowledgeIndex`.
