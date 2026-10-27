---
name: sql-analysis
description: "生成数据分析 SQL 语句。适用于 PostgreSQL 公共数据仓库查询、表关联、聚合统计等场景。"
---

# SQL 分析技能

当用户需要生成数据分析 SQL（查询、聚合、报表）时使用此技能。

## 可用工具

- **search_tables**: 语义层检索相关表卡（不确定有哪些表时先用它）
- **describe_table_card**: 查看表的列清单（列名、类型、注释）
- **find_join_path**: 查多表间的 JOIN 路径（基于已登记的外键边）
- **search_metrics**: 检索业务指标定义（口径、聚合方式）
- **find_verified_queries**: 检索已审核的相似问题 SQL 范例
- **execute_postgresql**: 在公共数据仓库（PostgreSQL）执行只读 SQL

## 分析流程

1. **了解表结构**  
   调用 `search_tables` 找到相关表 → 调用 `describe_table_card` 深入理解每张表的字段含义。

2. **理解表关系**  
   涉及多张表时，调用 `find_join_path` 确定 JOIN 关系（走已登记边，不自行臆测关联键）。

3. **参考既有资产**  
   调用 `search_metrics` 对齐业务口径；调用 `find_verified_queries` 参考相似问题的已审核 SQL。

4. **生成并验证 SQL**  
   写出 SQL 后用 `execute_postgresql` 实际执行，基于真实结果作答；报错则修正后重试。

## SQL 编写规范

### 结构
- 复杂查询优先用 **CTE（WITH 子句）** 组织，避免多层嵌套子查询
- 每段 CTE 用注释说明其用途
- 大表查询必须带 LIMIT，默认 1000 行

### 安全性
- 聚合运算注意**除零保护**：`NULLIF(denominator, 0)` 或 `COALESCE(..., 0)`
- 浮点除法保证精度：`ROUND(a * 1.0 / NULLIF(b, 0), 2)`

### PostgreSQL 方言
- 字符串匹配用 `ILIKE`（忽略大小写）或 `LIKE`
- 日期截断用 `date_trunc('day', ts)`；日期差用 `AGE()` 或直接相减
- 类型转换用 `::type` 或 `CAST(x AS type)`

### 窗口函数
- 排名：`ROW_NUMBER()` / `RANK()` / `DENSE_RANK()`
- 累计：`SUM(...) OVER (ORDER BY ...)`
- 移动平均：`AVG(...) OVER (ORDER BY ... ROWS BETWEEN N PRECEDING AND CURRENT ROW)`
- 同比/环比：`LAG(metric, N) OVER (PARTITION BY ... ORDER BY date)`

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
- 查询结果要点解读
```
