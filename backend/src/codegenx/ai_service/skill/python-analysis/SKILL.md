---
name: python-analysis
description: "生成 Python（pandas/numpy）数据分析代码，支持数据加载、清洗、聚合、统计检验等。"
---

# Python 数据分析技能

当用户需要生成 Python 数据分析代码（pandas/numpy/scipy）时使用此技能。

## 可用工具

- **describe_table** / **describe_csv**: 查看数据结构
- **sample_rows** / **sample_csv_rows**: 查看数据样例
- **describe_table_stats** / **describe_csv_stats**: 查看统计信息

## 分析流程

1. **确定数据源**  
   数据库表（MySQL）还是文件（CSV）？不同的数据源用不同的加载方式。

2. **了解数据结构**  
   调用 `describe_table`（MySQL）或 `describe_csv`（CSV）获取列名、类型、行数等信息。  
   调用 `sample_rows` 查看实际数据样例。

3. **确认分析目标**  
   向用户确认要做什么分析：描述性统计？分组对比？趋势分析？异常检测？关联分析？  
   不同目标对应不同的代码结构。

4. **生成分析代码**  
   包含数据加载、清洗、分析和结果输出四个阶段。

## 代码生成规范

### 数据加载
```python
# MySQL
import pandas as pd
from sqlalchemy import create_engine
engine = create_engine('mysql+pymysql://user:pass@host/db')
df = pd.read_sql("SELECT ... FROM table", engine)

# CSV
df = pd.read_csv('file.csv')
```
加载后第一步**必须**打印 `df.info()` 和 `df.head()` 确认数据正确。

### 数据清洗
- **缺失值**：数值用中位数/均值填充，类别用众数/"未知"，时间序列用前向填充
- **类型转换**：日期统一用 `pd.to_datetime()`，指定 `format` 参数加速
- **异常值**：IQR 法（Q1-1.5×IQR, Q3+1.5×IQR）或分位数截尾
- **去重**：根据业务主键去重
- 所有聚合操作注意 `dropna()` 的影响

### 大数据集
- 提示用 `chunksize` 分块读取：`pd.read_csv('large.csv', chunksize=100000)`
- 只读需要的列：`pd.read_csv('file.csv', usecols=['col1', 'col2'])`

### 代码质量
- 关键步骤加注释说明 **WHY**（为什么用中位数填充、为什么选 Mann-Whitney 而不是 t 检验）
- 变量名有业务含义，不要 `df1`, `df2`
- 中间结果打印形状和头几行，方便调试

## 常见分析模式

### 描述性统计
```python
df.describe(include='all')
df['category_col'].value_counts()
df.groupby('category')['numeric_col'].agg(['mean', 'median', 'std', 'count'])
```

### 分组对比
```python
# t 检验 / Mann-Whitney U
from scipy import stats
group_a = df[df['group'] == 'A']['metric']
group_b = df[df['group'] == 'B']['metric']
stats.ttest_ind(group_a, group_b)
```

### 时间序列
```python
df['date'] = pd.to_datetime(df['date'])
daily = df.set_index('date').resample('D').agg({'metric': 'sum'})
daily['ma7'] = daily['metric'].rolling(7).mean()  # 7日移动平均
```

### 透视表
```python
pd.pivot_table(df, values='metric', index='category', columns='month', aggfunc='sum')
```

## 输出格式

```
## 分析思路
<分析逻辑>

## 数据加载
```python
...
```

## 数据清洗
```python
...
```

## 分析代码
```python
...
```

## 结果说明
<预期输出和解读>
```
