---
name: modeling
description: "生成机器学习建模代码，支持分类、回归、聚类、时间序列预测任务，包含模型选择、训练、评估、调优。"
---

# 机器学习建模技能

当用户需要生成机器学习模型训练和评估代码时使用此技能。

## 可用工具

- **describe_table** / **describe_csv**: 了解数据结构
- **sample_rows**: 查看数据样例
- **describe_table_stats** / **describe_csv_stats**: 获取统计分布和缺失情况

## 分析流程

1. **确认任务类型**  
   分类（二分类/多分类）、回归、聚类、还是时间序列预测？

2. **了解数据**  
   调用 describe/sample/stats 工具获取字段信息、缺失情况、分布特征。

3. **推荐模型**  
   根据数据规模、特征类型、任务目标推荐 2-3 个候选模型并说明理由。

4. **生成完整建模代码**  
   数据准备 → 预处理 → 拆分 → 模型训练 → 评估 → 特征重要性。

## 模型选择指南

### 分类
| 场景 | 推荐模型 | 理由 |
|------|---------|------|
| 需要可解释性 | 逻辑回归 / 决策树 | 系数/规则透明 |
| 特征多、样本大 | XGBoost / LightGBM | 梯度提升，性能好 |
| 样本少、特征多 | 带 L1 正则的逻辑回归 | L1 自动选特征 |
| 类别不平衡 | LightGBM + scale_pos_weight | 内置样本权重 |
| 需要概率 | 逻辑回归 / 校准的 XGBoost | 天然输出概率 |

### 回归
| 场景 | 推荐模型 | 理由 |
|------|---------|------|
| 线性关系 | Linear / Ridge / Lasso | 简单可解释 |
| 非线性 | XGBoost / LightGBM 回归 | 树模型天然非线 |
| 长尾目标 | log 变换 + 线性回归 | 压缩长尾 |

### 聚类
| 场景 | 推荐模型 | 理由 |
|------|---------|------|
| K 已知 | K-Means | 可解释、快 |
| K 未知 | 层次聚类 + 树状图 | 可视化确定 K |
| 复杂形状 | DBSCAN | 自动识别噪声 |
| 高维 | PCA → K-Means | 降维去噪 |

### 时间序列
| 场景 | 推荐模型 | 理由 |
|------|---------|------|
| 趋势+季节性 | Prophet | 自动检测、鲁棒 |
| 短期+多变量 | LightGBM + 时间特征工程 | 特征工程后效果好 |
| 长期+深度学习 | LSTM | 长序列建模 |

## 代码生成规范

### 拆分策略
- **分类/回归**：`train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)`（分类必须 stratify）
- **时间序列**：绝不能用随机拆分！按时间点切分：`train = df[df['date'] < '2024-01-01']`

### 模型训练
- 包含交叉验证：`cross_val_score(model, X_train, y_train, cv=5, scoring='...')`
- 打印 CV 均值和标准差，不要只看单次分数

### 评估指标（按任务选）
- **分类**：混淆矩阵、Precision/Recall/F1、ROC-AUC
  - 类别不平衡时，PR-AUC 比 ROC-AUC 更有参考价值
- **回归**：MAE、RMSE、R²、MAPE
- **聚类**：轮廓系数 (Silhouette)、Davies-Bouldin 指数、肘部法图
- **时间序列**：MAE、RMSE、MAPE、残差自相关检查

### 特征重要性
- 树模型：`model.feature_importances_`
- 线性模型：`abs(model.coef_)`
- 输出 Top 10-15 重要特征及分数

### 模型保存
```python
import joblib
joblib.dump(best_model, 'model.pkl')
```

### 超参调优
- 优先推荐 Optuna（比 GridSearch 快 10 倍+）：
```python
import optuna
def objective(trial):
    params = {
        'n_estimators': trial.suggest_int('n_estimators', 100, 1000),
        'max_depth': trial.suggest_int('max_depth', 3, 15),
        'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.3, log=True),
    }
    model = XGBClassifier(**params)
    return cross_val_score(model, X_train, y_train, cv=5, scoring='f1').mean()
study = optuna.create_study(direction='maximize')
study.optimize(objective, n_trials=50)
```

## 关键注意事项

1. **时间序列禁止随机拆分**——会造成数据泄露
2. **类别不平衡**（正负比 < 1:3）→ SMOTE、样本权重、阈值调整三选一
3. **数据泄露**：训练集和测试集不得有信息交叉（所有 fit 只 fit 训练集）
4. **调参粒度**：先粗调（学习率、树深度），再细调（正则化参数）

## 输出格式

```
## 任务判断
- 类型：<分类/回归/聚类/时间序列>
- 目标变量：<列名>
- 评估指标：<指标> — <理由>

## 模型推荐
| 模型 | 适用理由 | 优点 | 缺点 |
|------|---------|------|------|

## 建模代码
```python
# 1. 导入库
# 2. 加载数据
# 3. 预处理
# 4. 拆分
# 5. 训练 + 交叉验证
# 6. 评估
# 7. 特征重要性
# 8. 模型保存
```

## 使用说明
<运行代码的步骤、需要安装的库>
```
