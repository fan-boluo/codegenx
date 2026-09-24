---
name: sql-analysis
description: "生成数据分析 SQL 语句。适用于 MySQL 数据库查询、表关联、聚合统计等场景。"
---

# SQL 分析技能

当用户需要生成数据分析 SQL（查询、聚合、报表）时使用此技能。

## 可用工具

- **list_tables**: 查看数据库中有哪些表
- **describe_table**: 查看表的列名、类型、注释、索引
- **sample_rows**: 采样查看表数据
- **describe_table_stats**: 查看表的数值统计、分布、缺失情况
- **get_table_relationships**: 查看表间外键和推断关联关系
- **guess_analysis_task**: 不确定分析方向时，自动推断建议

## 分析流程

1. **了解表结构**  
   调用 `list_tables` 找到相关表 → 调用 `describe_table` 深入理解每张表的字段含义。  
   如果用户没有指定表，先列出所有表让用户选择。

2. **查看数据样例**  
   对每张相关表调用 `sample_rows`（默认 10 行），理解字段的实际值、格式和典型内容。

3. **理解表关系**  
   如果涉及多张表，调用 `get_table_relationships` 确定 JOIN 关系。  
   如果没有显式外键，通过字段名模式推断（如 `user_id` → `users.id`）。

4. **明确需求**  
   如果用户需求模糊，调用 `guess_analysis_task` 给用户提供分析方向建议。  
   需要和用户确认的细节：时间范围？聚合粒度（按天/周/月）？排序方式？需要多少行结果？

5. **生成 SQL**  
   写出完整可运行的 SQL，关键逻辑用注释标注 WHY。

## SQL 编写规范

### 结构
- 复杂查询优先用 **CTE（WITH 子句）** 组织，避免多层嵌套子查询
- 每段 CTE 用注释说明其用途
- 大表查询必须带 LIMIT，默认 1000 行

### 安全性
- 聚合运算注意**除零保护**：`NULLIF(denominator, 0)` 或 `COALESCE(..., 0)`
- 浮点除法保证精度：`ROUND(a * 1.0 / NULLIF(b, 0), 2)`

### 可移植性
- 字符串匹配优先用 `LIKE` 或 `INSTR`，避免正则（MySQL/PostgreSQL 语法不同）
- 日期函数优先用标准形式，避免数据库特有函数

### 窗口函数
- 排名：`ROW_NUMBER()` / `RANK()` / `DENSE_RANK()`
- 累计：`SUM(...) OVER (ORDER BY ...)`
- 移动平均：`AVG(...) OVER (ORDER BY ... ROWS BETWEEN N PRECEDING AND CURRENT ROW)`
- 同比/环比：`LAG(metric, N) OVER (PARTITION BY ... ORDER BY date)`

### 参数占位符
用 `{start_date}`、`{end_date}`、`{top_n}`、`{threshold}` 等标记需用户替换的参数。

### JOIN 选择
- `INNER JOIN`：只关心两表都有的记录
- `LEFT JOIN`：保留左表全部，右表无匹配填 NULL
- 多表 JOIN 注意笛卡尔积——先确认关联键的唯一性

## 输出格式

```
## 分析思路
<简要说明：查什么、从哪些表、怎么关联、为什么这么写>

## SQL 代码
```sql
-- 步骤1: 获取基础数据
WITH base AS (
  ...
)
-- 步骤2: 聚合计算
SELECT ...
FROM base
WHERE ...
GROUP BY ...
ORDER BY ...
LIMIT 1000
```

## 说明
- 关键逻辑要点
- 替换参数说明
- 预期输出样例
```
