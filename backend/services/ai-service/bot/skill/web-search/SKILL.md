---
name: web-search
description: "搜索网页内容、获取指定 URL 信息，用于查找文档、数据集、API 参考等数据应用开发所需资源"
metadata: { "openclaw": { "emoji": "🔍", "always": true } }
---

# Web Search

Search the web for information and fetch content from URLs.

## Tools Available

- **web_search**: Search for information (if available)
- **web_fetch**: Fetch content from specific URLs

## Search Strategies

1. **Direct URL fetch**: If user provides a URL, use web_fetch directly
2. **Search query**: Use web_search to find relevant pages
3. **Multiple sources**: Fetch multiple URLs for comprehensive info

## Best Practices

- Verify information from multiple sources
- Cite sources in your response
- Summarize content clearly
- Extract key information relevant to the query

## Example Queries

- "Search for Python asyncio tutorial"
- "What's on the Python homepage?"
- "Find documentation for FastAPI"
- "Get the latest news about AI"
