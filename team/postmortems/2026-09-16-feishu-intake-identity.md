# Lark 已接收但不回复：入口身份与配对授权不一致

## 症状与证据

2026-09-16 部署团队治理后，用户 @ 机器人出现短暂接收表情，随后无回复。网关先记录消息到达，紧接着记录 Unauthorized user；消息没有进入模型。

## 根因

团队治理将真实请求者的主标识统一为应用 open_id，既有聊天配对授权仍使用此前的租户 user_id。管理员名单与聊天准入是两层独立检查：配置管理员不会自动授予入口访问。旧验收验证了入站映射、治理授权及工具调用，却遗漏两者之间的网关准入判断。

## 修复

根据已核实的同一真人身份，纠正部署中的既有配对记录标识，保留原授权元数据和授权人数。没有开启全局访问，没有扩大管理权限，也不需重启服务。实际身份及配对记录只保存在私有运行目录。

## 验证与防回归

- 入站回归串联真实 GatewayRunner 授权判断和临时 PairingStore：旧类型身份拒绝、正确 open_id 通过、陌生用户仍拒绝。
- team/manage.py verify 增加正式运行用户的只读管理员聊天入口预检，避免资产和工具测试通过却漏掉真实准入。
- 从已授权 my bots 群的登录客户端发送简单验收消息，机器人实际回复“消息回复已恢复。”；界面与消息 API 双重回读成功。
- 私有证据：team/.local/intake-recovery-chat.json、intake-recovery-thread.json；修复前配对备份在 team/.local/feishu-approved-before-id-fix.json，禁止提交。

## 排查入口

先看 data/logs/gateway.log 是否按顺序出现 Received raw message、inbound message、response ready。若有 Unauthorized user，核对当前 source 的 ID 类型与配对/访问名单，而非重配模型、词典或放开全局权限。接收表情只说明消息到达适配器，不说明网关已批准执行。
