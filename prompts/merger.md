# 增量 Merge Prompt (v2 — 覆盖式)

## 任务

你将收到:
1. 现有 `persona.md` 内容(完整 markdown)
2. **新消息的 analyzer JSON 分析结果**
3. **新消息的硬指标增量**(只算新消息的 stats)
4. **新消息的聊天原文语料**

你的任务是**重新生成一份完整的 persona.md**,覆盖原文件。**不要输出 patch,不要 append 段落,输出完整新文件**。

---

## 原则

1. **保留有价值的旧 Layer 内容**:旧 persona 里的 Layer 1 身份、Layer 5 边界等如果有原文支撑就保留;Layer 0 核心性格、Layer 2 表达风格、Layer 3 行为模式需要根据新数据**更新**。
2. **冲突解决**:新证据推翻旧描述时,**以新证据为准**。在 Layer 0 / Layer 2 里用新规则覆盖旧规则。
3. **时间标注**:在文件顶部 metadata 加 `> 上次蒸馏: {old_date}; 本次更新: {new_date}; 新增消息: {N} 条`
4. **不丢失用户手动标签**:用户填的 profile / tags / impression 永远在 Layer 1。
5. **整体长度 3-4KB**:不要把 persona 写成长篇散文。更新后跟原文件大小差不多,不要膨胀。

---

## 工作流程

1. 读旧 persona.md,提取已经稳定的部分(身份、长期兴趣、明确的雷区)
2. 读新 analyzer JSON,提取"新增/变化的特征"
3. 合并:
   - Layer 0:旧规则 + 新规则(冲突时新覆盖旧),按主题归类
   - Layer 1:保留(可能更新年龄/职业)
   - Layer 2:口头禅、句式如果新数据强化了旧的就更新,加新观察
   - Layer 3:行为模式如果新数据推翻就更新
   - Layer 4:兴趣列表如果新话题出现就加
   - Layer 5:雷区基本稳定,新数据加强就保留
4. 输出完整新 persona.md

---

## 输出

**只输出新 persona.md 的完整 markdown 内容**。不要 ```markdown``` 包裹,不要"以下是更新后的 persona.md"前缀,不要"完"后缀。
