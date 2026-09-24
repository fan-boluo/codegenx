---
name: plot
description: "生成数据可视化代码（matplotlib/seaborn/plotly），支持柱状图、折线图、散点图、热力图等。"
---

# 数据可视化技能

当用户需要生成图表/可视化代码时使用此技能。

## 可用工具

- **describe_table** / **describe_csv**: 了解字段类型
- **sample_rows** / **sample_csv_rows**: 查看数据样例

## 分析流程

1. **确认绘图目标**  
   用户想看什么？对比、分布、趋势、构成、还是关系？

2. **了解数据**  
   调用 describe/sample 工具获取字段名称、数据类型、取值范围。  
   这些决定图表选择。

3. **选择图表类型**  
   根据字段类型和分析目标自动推荐（见下方指南）。

4. **生成代码**  
   数据先用 pandas 聚合 → 再传进绘图函数。数据和绘图分离。

## 图表选择指南

| 分析目标 | 推荐图表 | 适用数据 |
|---------|---------|---------|
| 比较数值大小 | 柱状图 (bar)、条形图 (barh) | 类别 × 数值 |
| 展示占比/构成 | 饼图（≤6类）、环形图、堆叠柱状图 | 类别 × 数值 |
| 展示分布 | 直方图 (hist)、箱线图 (boxplot)、小提琴图 | 数值列 |
| 展示趋势 | 折线图 (line)、面积图 (area) | 时间 × 数值 |
| 展示关系 | 散点图 (scatter)、气泡图、热力图 | 数值 × 数值 |
| 排名 | 排序条形图 | 类别 × 数值 |
| 多维度对比 | 分组柱状图、雷达图 | 类别 × 多数值 |

**饼图约束**：类别 ≤ 6 个用饼图，更多用横向条形图（更易阅读）。  
**折线图约束**：系列 ≤ 5 条，超出时分面或单独成图。

## 代码生成规范

### 默认库选择
- **优先用 plotly**（交互式，支持缩放/悬停）
- 用户明确要静态图时用 matplotlib/seaborn

### 中文字体
```python
# matplotlib
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# plotly
fig.update_layout(title_font=dict(family='SimHei'))
```

### 配色
- 分类用 `Set2` 或 `tab10`（颜色区分度高）
- 连续用 `Viridis` 或 `Blues`（色盲友好）
- 避免红绿对比（红绿色盲最常见）

### 必须元素
- 标题（说明图表内容）
- X/Y 轴标签（带单位）
- 图例（多系列时）
- 数值标签：柱状图顶部标注数值，饼图标注百分比

### 尺寸和输出
- 默认尺寸 `(12, 6)` 英寸（16:9 比例）
- 代码结构：数据聚合 → 传入数据 → 设置样式 → 显示/保存

## 常见图表模板

### 柱状图（比较）
```python
import plotly.express as px
fig = px.bar(df_agg, x='category', y='value', color='group', barmode='group',
             title='标题', text_auto='.1f')
fig.update_layout(xaxis_title='类别', yaxis_title='数值', title_font=dict(family='SimHei'))
fig.show()
```

### 折线图（趋势）
```python
fig = px.line(df_time, x='date', y='metric', color='category',
              title='趋势图', markers=True)
fig.show()
```

### 散点图（关系）
```python
fig = px.scatter(df, x='x_col', y='y_col', color='category',
                 size='size_col', hover_data=['label'],
                 title='关系图', trendline='ols')
fig.show()
```

### 热力图（相关性）
```python
import seaborn as sns
corr = df.corr(numeric_only=True)
sns.heatmap(corr, annot=True, fmt='.2f', cmap='RdBu_r', center=0)
```

## 输出格式

```
## 图表选择
推荐图表：<图表类型> — <选择理由>

## 绘图代码
```python
# 1. 数据准备
# 2. 绘图
# 3. 显示/保存
```
```
