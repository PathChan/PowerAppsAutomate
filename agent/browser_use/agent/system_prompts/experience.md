<experience>
你有一个经验系统。通过维护 <experience.md> 文件来积累每次任务的经验教训。

**什么是经验**：
- 完成类似任务时学到的有效步骤和模式
- 遇到过的陷阱和解决方法
- 特定网站/页面的操作技巧（如登录方式、表单填写注意事项）
- 对任务效率有幫助的通用规则

**如何使用经验**：
- 每次执行任务前，阅读 <experience.md> 中的历史经验
- 将相关经验应用到当前任务中
- 新学到的经验及时追加到 <experience.md>

**经验维护规则**：
- 格式：每一条经验用 `- [经验分类] 具体描述` 的格式
- 分类包括：`[页面操作经验]`、`[编码经验]`、`[表格操作经验]` 等
- 避免重复记录相同的经验
- 优先记录可复用的经验，而非一次性操作细节

## 积累的经验

### PowerApps 公式编辑器（Monaco）操作

⚠️ **核心事实**：PowerApps 公式栏用 Monaco Editor，其隐藏 `<textarea>` 无法通过任何 CDP mouse event / focus 直接激活。
**唯一可靠的聚焦方式** = 点击 `#formulaBarContainer > button`。

**代码已自动处理：** `default_action_watchdog.py` 在每次 CDP 点击后，都会自动检测页面是否有
`#formulaBarContainer > button`，如果有就点击它来触发 Monaco 焦点转移。
所以你用普通 `click(index)` 点击任何元素，公式栏都会自动获得焦点。

#### 标准的 Monaco 输入流程（AI 必须遵循）

1. 在 DOM 快照中找到 `#formulaBarContainer` 内部的 `[index]` 按钮元素
2. `click(index)` — 点击公式栏按钮，代码会自动触发 Monaco 聚焦
3. `input_text(index, text="公式内容")` — 输入到已聚焦的 Monaco textarea

⚠️ `input_text(clear=True)` 无法清除 Monaco 已有内容（textarea 是隐藏的）。如需清空，用 evaluate：
```
evaluate(js="document.querySelector('#formulaBarContainer > button')?.click();")
```
然后在同一轮 action 中用 `input_text(text="新内容", clear=False)` 输入。

#### 🚫 禁止事项（已验证全部无效）
- ❌ 禁止直接 click() 在 `.overflow-guard` 或 `.monaco-editor` 上（CDP mouse event 不行）
- ❌ 禁止 evaluate 调 `monaco.editor.getEditors()`（PowerApps 的 Monaco 实例无法访问）
- ❌ 禁止 `.inputarea.focus()`（隐藏 textarea 不受理 CDP focus）
- ❌ 禁止坐标点击 Monaco 区域（框能完美覆盖但点不进去）

### PowerApps 属性搜索框操作

- [页面操作经验] 使用 `document.querySelector("#powerapps-property-combo-box")` 找到属性搜索输入框
- 不要点右边的下拉箭头，直接输入属性英文名称（如 `Width`、`Height`、`X`、`Y`、`Text`）

- 输入后等待下拉建议出现，点击正确的建议项（会在 DOM 快照中显示为新的 *[index] 元素）
- 选中后公式编辑器会自动加载该属性的当前值
- [页面操作经验] 访问需要Microsoft认证的Power Apps编辑器前，必须先完成登录步骤（提供凭据或使用已有的登录会话），否则会被重定向到登录页面而无法访问。
- [页面操作经验] 等待PowerApps画布编辑器加载完成时，应通过检测左侧树视图（如树节点元素）、顶部工具栏（如命令栏）和中间画布区域（如画布容器）的存在来判断，而不是仅依赖超时休眠，避免因网络延迟导致后续操作失败。
- [页面操作经验] 在PowerApps编辑器中点击顶部功能选项卡（如“插入”）前，需先等待画布编辑器完全加载（检测树视图节点、顶部命令栏和画布容器），然后从DOM快照中找到选项卡对应的[index]元素后直接click，避免因未加载或选择错误元素导致操作失败。
- [页面操作经验] 在PowerApps编辑器的“插入”面板中查找控件时，先通过DOM快照找到并点击“插入”选项卡，等待面板内容加载完成后，再在DOM快照中找到对应控件（如“按钮”）的[index]元素进行单击，避免因面板未展开或控件未加载导致点击失败。
- [页面操作经验] 在PowerApps画布应用编辑页面，系统不会自动添加默认按钮到画布中央，应避免依赖此等待；需通过点击“插入”选项卡并选择相应控件来主动添加。
- [页面操作经验] 在PowerApps画布中选中已有控件时，应先确保画布加载完成，然后在DOM快照中找到该控件的可交互元素（通常带有`control`或控件类型类名），直接click即可；如果单击后右侧属性面板未更新，可先点击画布空白区域再单击控件，或使用evaluate执行`canvasApp.selectControl(controlName)`等方法确保选中。
- [页面操作经验] 在PowerApps右侧属性面板中操作属性值时，应先通过DOM快照找到该属性对应的公式编辑器区域（`.monaco-editor`或`.overflow-guard`元素），然后复用Monaco编辑器操作策略（点击聚焦后输入或用评估脚本设置值），而非寻找独立的输入框。
- [页面操作经验] 在PowerApps公式编辑器中，使用`evaluate`执行JavaScript（`editor.setValue('')`再`editor.setValue('新值')`）来清空并设置文本比`input_text(clear=True)`更可靠，因为Monaco编辑器无法通过CDP清除已有内容。
- [页面操作经验] 在PowerApps编辑器中，若使用`input_text`在非Monaco输入框（如属性值输入框）输入文本后，需按Enter或点击画布空白处来确认提交，否则输入内容可能不会被保存。
- [页面操作经验] 在PowerApps编辑器中，若Monaco编辑器位于跨域iframe中导致evaluate查询失败，应使用点击`.overflow-guard`索引元素聚焦后，立即`input_text`输入（不清除），再点击画布空白处或按Enter提交，避免输入未提交导致值未更新。
- [页面操作经验] 确认PowerApps画布编辑器加载完成时，应优先检测主容器元素（如 `.appc-canvas-editor`）的DOM存在性，而非依赖固定超时，避免因网络延迟导致后续操作失败。
- [页面操作经验] 点击PowerApps顶部“插入”选项卡后，不要立即执行后续点击，应等待插入面板中的具体控件列表（如“按钮”等）出现在DOM快照中，否则可能因面板未完全加载导致后续操作失败。
- [页面操作经验] 在PowerApps编辑器的插入面板中，点击“插入”选项卡后，必须等待插入面板内的具体控件列表（例如“按钮”元素）出现在DOM快照中，再进行点击，否则因面板未加载完整会导致点击失败。
</experience>