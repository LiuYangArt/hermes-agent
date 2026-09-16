# API 核验记录

核对日期：2026-09-15。

官方来源：
- https://openai.com/index/introducing-chatgpt-images-2-5/ — 2.5 发布，Flare 和 Sunburst。
- https://developers.openai.com/api/docs/guides/tools-image-generation — Responses 工具 model、action、tool_choice、图片输入与返回格式。
- https://developers.openai.com/api/docs/guides/image-prompting#model-parameters — 已实际打开 GPT Image 2.5 页签，核对完整参数。

## 2.5 官方设置

模型：gpt-image-2.5-flare（速度优先），gpt-image-2.5-sunburst（精细质量优先）。
质量：auto / low / medium / high / xhigh / max。
背景：auto / opaque / transparent；透明需 PNG 或 WebP。
尺寸 auto 或 WIDTHxHEIGHT：每边不超过 3840，两边均为 16 的倍数，长短边比例不超过 3:1，总像素 655360 至 8294400（含端点）。大于 3686400 像素的输出为实验能力。

| 用途 | 尺寸 |
|---|---|
| 方形 | 1024x1024 |
| 横版 | 1536x1024 |
| 竖版 | 1024x1536 |
| 2K 方形 | 2048x2048 |
| 2K 横版 | 2048x1152 |
| 4K 横版 | 3840x2160 |
| 4K 竖版 | 2160x3840 |

1920x1080 不符合两边均为 16 倍数的要求，不直接发送；向用户说明并选择其接受的合法尺寸（例如 1920x1088），不得默默更改精确尺寸。

## Helius Responses 请求

顶层 model=cx/gpt-6-astra；tools 中 type=image_generation、model=完整 2.5 名称、action=generate/edit；tool_choice={type:image_generation} 强制生图。
输入为 role=user 的 content 列表，包含 input_text；图生图增加 input_image，image_url 为本地图片的 data URL。返回 output 中 image_generation_call.result 为 base64。

此前使用简称 gpt-image-2.5 成功返回图片，但没有实际图像模型字段，并将请求 1024x1024 输出成 1254x1254。因此不将简称成功当作正式型号或参数遵循的证据。正式型号调用也要核对实际图片尺寸、动作和 alpha。模型列表不一定列出工具内部图像模型。

完整 HTTP 响应并不证明后台未重映射模型；报告区分 requested_image_model 与 reported_image_model，后者缺失时明确为未知。当前配置若不再是 Helius，停止并说明，避免将图片送到其他 Provider。

## 本技能验收

- quick_validate.py：通过。
- 离线行为检查：合法/非法尺寸、图生图缺少原图、显式文生图拒绝图片、多模态图片数据构造均通过。
- 正式 gpt-image-2.5-flare + action=generate + tool_choice：成功返回 PNG，实际 1536x1024，与请求相同。返回 action=generate，未返回内部图像模型名。
- action=edit：两次返回 HTTP 503 / chat_admission_busy，未拿到图片。仅请求构造通过，端到端图生图尚未验证；不要宣称已验证。服务端恢复后用下方同一输入做一次真实验证并更新此记录。
- Sunburst、透明背景、JPEG/WebP、自定义 4K：依据官方参数支持，尚未在本 Provider 实测。

验证产物：C:/Users/LiuYang/.codex/generated_images/helius/skill-validation-20260915/
文生图报告：flare-generate.json；提示词 generate.txt / edit.txt。

复验命令（输出已存在时换新名字）：
```powershell
python C:/Users/LiuYang/.codex/skills/helius-imagegen/scripts/helius_imagegen.py --prompt-file C:/Users/LiuYang/.codex/generated_images/helius/skill-validation-20260915/edit.txt --mode edit --image C:/Users/LiuYang/.codex/generated_images/helius/skill-validation-20260915/flare-generate.png --size 1536x1024 --quality low --out C:/Users/LiuYang/.codex/generated_images/helius/skill-validation-20260915/flare-edit.png
```

## Docker Hermes 验收（2026-09-15）

从 Lark 群 @ 请求，Hermes 读取技能、执行脚本、调用 vision_analyze 并发送原图成功。Flare generate，1536x1024，low，PNG，实际尺寸一致，warnings=[]；模型身份未返回，保持 unknown。图生图仅请求构造验证，未做新的真实编辑调用。默认工作目录现已调整为 /opt/data/imagegen/helius，符合文件工具写入安全根目录；第一次验收使用旧 /workspace 示例触发审批，后经一次性批准生成。
