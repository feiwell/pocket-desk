---
name: pocketdesk
description: Preview and safely apply iPhone home-screen classification with project-native pocketdesk commands, preserving the Dock and verifying the actual read-back.
---

# pocketdesk：安全桌面归类

在项目安装环境使用 `pocketdesk`；不要依赖私有设备目录，不上传真实布局、备份、配对文件、凭据或本机账户信息。

## 执行顺序

1. 明确需要全量重排（`plan`）还是只收尾页散图标（`merge`）。
2. 设备可达时可用 `pocketdesk check`、`pocketdesk list` 查看；失败停止，不并发隧道、不循环重试。
3. 先生成预览（默认 dry-run），向用户展示，并获得明确写入同意：

   ```bash
   pocketdesk plan --rules fine
   pocketdesk merge --assignments mapping.json
   # 已有快照可离线预览，不连接设备：
   pocketdesk merge --assignments mapping.json --from snapshot.json --out proposed.json
   ```

4. 仅在明确同意后执行对应的 `--apply`：

   ```bash
   pocketdesk merge --assignments mapping.json --apply
   # 全量整理则用 pocketdesk plan --rules fine --apply
   ```

   `--from` 禁止与 `--apply` 合用。写入前自动备份；保留输出的快照路径。
5. 必须等待精确回读通过。非零退出、超时、断线或不匹配都不能宣称成功。
6. 需要恢复时先说明风险并获得同意，再执行 `pocketdesk restore <snapshot.json>`；它会先备份当前布局，再写入并回读。

## mapping.json 与布局约束

```json
{
  "com.example.reader": "Reading",
  "com.example.utility": "Tools"
}
```

键是原始 `displayIdentifier`（bundle id），不是 App 显示名称；值是已有文件夹的精确名称。
必须完整覆盖最后一个含文件夹页面之后的所有散图标。未知 id/目标、重名文件夹、重复键或图标、
前面页面的散图标、widget/嵌套文件夹/异常结构都应安全拒绝，不跳过、不猜测、不丢图标。

- `[dock, page1, page2, ...]` 的第 0 项是 **Dock，不是页**。
- 增量操作保留原始 Dock、已有成员/文件夹顺序和已有页边界；按散图标原顺序追加到目标文件夹最后一个内部页，清除收空的尾部主屏页。
- 计划保留原始字典和 `datetime`，禁止 JSON 往返后写入。新快照带日期类型标记；旧快照仅解析应用的 `iconModDate`，非法日期拒绝。

## API 与结果判定

使用官方 SpringBoard API；构造不等于连接：

```python
svc = SpringBoardServicesService(tunnel.rsd)
try:
    await svc.connect()
    raw = await svc.get_icon_state(format_version="2")
    # 明确同意并备份后：await svc.set_icon_state(raw_plan)
finally:
    await svc.close()
```

`read`/`write`/`metrics` 都必须保证 `finally` 关闭，包括连接/调用异常。
不要手拼 plist 或 framing：旧超时路径缺少 `build_plist()` 添加的四字节长度头，
并不是 XML 格式不支持；优先官方 `set_icon_state(raw_plan)`。

精确回读比较 Dock 身份/顺序、全量图标集合与重复、文件夹归属/顺序、内部页和主屏页边界，
不能只比数量。计划中 Dock 原始字段不变；回读容许 row 包装及名称/版本/日期等非布局元数据刷新。
若系统自行分页/增补图标，也保守报不匹配，不自动回滚。

**超时或断线意味着结果未知**：可能已经生效。不得误报成功，也不得盲目重试；保留备份，
连接恢复后先检查/回读，再请求任何纠正写入的同意。

## 离线回归

```bash
python3 -m unittest discover -s tests -v
```

仅用合成数据和 mock，不读真实备份、不连设备。详见 README 与 `docs/PITFALLS.md`。
