# Browser Use

AI 代理自动操作浏览器的框架。基于 CDP (Chrome DevTools Protocol)，支持多种 LLM。

## 目录结构

```
browser_use/
├── agent/              # 核心代理逻辑
│   ├── service.py      # Agent 主循环 - 思考→行动→观察
│   ├── views.py        # 数据模型 (AgentState, ActionResult 等)
│   ├── prompts.py      # 系统提示词模板
│   ├── judge.py        # 评估器 - 判断任务是否完成
│   ├── gif.py          # 生成操作历史 GIF
│   ├── variable_detector.py  # 从历史中提取可复用变量
│   ├── cloud_events.py # 云端事件追踪
│   └── message_manager/ # 消息管理 & 历史压缩
│
├── browser/            # 浏览器控制层
│   ├── session.py      # BrowserSession - CDP 会话管理
│   ├── session_manager.py  # 会话池同步
│   ├── profile.py      # 浏览器配置 (Chrome flags, 代理等)
│   ├── events.py       # 事件定义 (导航/点击/滚动等)
│   ├── views.py        # 浏览器状态数据模型
│   ├── demo_mode.py    # 浏览器内演示面板
│   ├── video_recorder.py    # 录屏
│   ├── cloud/          # 云端浏览器服务
│   └── watchdogs/      # 后台监控 (验证码/弹窗/下载等)
│
├── dom/                # DOM 分析与序列化
│   ├── service.py      # DOM 树提取
│   ├── views.py        # DOM 数据结构
│   ├── enhanced_snapshot.py  # 增强可访问性树快照
│   ├── markdown_extractor.py # 页面转 Markdown
│   └── serializer/     # DOM 序列化 (给 LLM 的文本表示)
│
├── tools/              # 动作/工具系统
│   ├── service.py      # Tools (Controller) - 执行浏览器操作
│   ├── views.py        # 动作模型 (点击/输入/导航等)
│   ├── registry/       # 动作注册 & 管理
│   └── extraction/     # 内容提取工具
│
├── llm/                # LLM 集成 (多种供应商)
│   ├── base.py         # BaseChatModel 协议
│   ├── messages.py     # 消息类型
│   ├── views.py        # 响应模型
│   ├── anthropic/      # Claude
│   ├── openai/         # GPT 系列
│   ├── google/         # Gemini
│   ├── deepseek/       # DeepSeek
│   ├── ollama/         # 本地 Ollama
│   ├── litellm/        # LiteLLM 统一接口
│   └── ... (其他: AWS, Azure, Groq, Mistral 等)
│
├── mcp/                # MCP 服务器 (Model Context Protocol)
│   ├── server.py       # MCP 服务端
│   ├── client.py       # MCP 客户端
│   └── controller.py   # MCP 控制器
│
├── actor/              # 高级页面/元素抽象
│   ├── page.py         # Page 类 (标签页操作)
│   ├── element.py      # Element 类
│   ├── mouse.py        # 鼠标操作
│   └── playground/     # 示例脚本
│
├── filesystem/         # 文件系统操作
│   └── file_system.py
│
├── screenshots/        # 截图存储服务
├── skills/             # 云端技能系统
├── telemetry/          # 匿名遥测 (PostHog)
├── tokens/             # Token 用量 & 成本追踪
├── sync/               # 云端同步服务
├── sandbox/            # 沙箱代码执行
├── integrations/gmail/ # Gmail 集成 (2FA 验证码等)
├── skill_cli/          # Skill CLI 命令行工具
│
├── config.py           # 配置系统
├── cli.py              # CLI 入口
├── utils.py            # 工具函数
├── logging_config.py   # 日志配置
├── observability.py    # 可观测性 (lmnr 集成)
├── exceptions.py       # 自定义异常
└── init_cmd.py         # 项目初始化模板
```

## 核心流程

1. **Agent** 接收用户任务 → 调用 LLM 决定下一步动作
2. **Tools** 执行浏览器操作 (导航/点击/输入等)
3. **DOM** 提取页面状态 → 序列化为 LLM 可读文本
4. **BrowserSession** 通过 CDP 控制浏览器
5. 循环直到任务完成或失败